#!/usr/bin/env python3
"""Execute the packaged ARM machine code. The host supplies filesystem, keys and LCD.
Build with the Ndless SDK first. Test dependencies: unicorn, pyelftools and Pillow.
"""
from __future__ import annotations
import contextlib, hashlib, shutil, struct, sys, tempfile, zlib
from pathlib import Path
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UcError, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE, UC_HOOK_MEM_READ
from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3,UC_ARM_REG_SP,UC_ARM_REG_LR,UC_ARM_REG_PC
from make_samples import create

ROOT=Path(__file__).resolve().parents[1]
BASE,STACK,HEAP,STOP=0x100000,0x800000,0x1000000,0x3000000
REGS=(UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3)

def machine_code(path):
    data=path.read_bytes(); magic,version,size,nr,nf,extra,alloc,entry=struct.unpack_from('<8I',data)
    assert (magic,version,size)==(0x6e68655a,1,len(data)) and entry==0
    reloc=struct.unpack_from(f'<{nr}I',data,32); off=32+4*(nr+nf)+extra
    assert reloc[0]==3
    image=bytearray(zlib.decompress(data[off:])); image+=bytes(alloc-off-len(image))
    def apply(at,zero=False):
        value=struct.unpack_from('<I',image,at)[0]; struct.pack_into('<I',image,at,0 if zero else value+BASE)
    for raw in reloc[1:]:
        code,at=raw&255,raw>>8
        if code==0: apply(at)
        elif code==1:
            while struct.unpack_from('<I',image,at)[0]!=0xffffffff: apply(at); at+=4
        elif code==2: apply(at,True)
        elif code!=4: raise ValueError(f'Unsupported relocation {code}')
    return bytes(image)


class Machine:
    def __init__(self,folder,commands,stalled=False,subtype=2,timer_registers=None,geometry_failures=0):
        self.mu=Uc(UC_ARCH_ARM,UC_MODE_ARM); self.folder=folder; self.keys=iter(commands)
        image=machine_code(ROOT/'Folio.tns'); self.mu.mem_map(BASE,(len(image)+4095)&~4095); self.mu.mem_write(BASE,image)
        self.mu.mem_map(STACK,0x40000); self.mu.mem_map(HEAP,0xa00000); self.mu.mem_map(STOP,4096)
        self.heap=[(HEAP,0xa00000)]; self.sizes={}; self.handles={}; self.slot=0x5000000
        self.realloc_limit=None
        self.messages=[]; self.screen=b''; self.snapshots={}; self.cut=False; self.io_reads=0
        with (ROOT/'build/Folio.elf').open('rb') as f:
            symbols=ELFFile(f).get_section_by_name('.symtab'); self.syms={s.name:s['st_value']+BASE for s in symbols.iter_symbols() if s.name}
        self.entrybuf=self.alloc(1024)
        self.docs=self.alloc(32);self.mu.mem_write(self.docs,b'/documents/\0')
        self.kptype=self.alloc(8);self.mu.mem_write(self.kptype,b'\x04')
        self.stalled=stalled;self.subtype=subtype;self.native_polls=0
        self.us=0;self.hold_until=0;self.hold_action=None;self.touch_error=False
        self.geometry_failures=geometry_failures;self.geometry_reads=0
        clocks={0x90010000:0,0x900c0000:1,0x900d0000:2}
        for addr in (*clocks,0x900e0000,0x90140000,0x900b0000):
            self.mu.mem_map(addr,4096)
        self.touch=None;self.page=4
        self.initial_registers={}
        for base,speed in clocks.items():
            self.initial_registers.update({base:0,base+8:0,base+0x20:0,base+0x28:0,base+0x80:speed})
            for offset in (4,0x24):
                self.mu.hook_add(UC_HOOK_MEM_READ,self.timer_read,base,begin=base+offset,end=base+offset+3)
        self.initial_registers.update(timer_registers or {})
        for address,value in self.initial_registers.items():
            self.mu.mem_write(address,struct.pack('<I',value))
        self.timer_reads=set()
        poll=self.syms['device_poll']
        self.mu.hook_add(UC_HOOK_CODE,self.advance_inputs,begin=poll,end=poll)

        for name in ('malloc','calloc','realloc','free','fopen','fclose','fread','fwrite','fseek','ftell','fflush',
                     'nuc_opendir','nuc_readdir','nuc_closedir','lcd_blit','lcd_init','device_message','_show_msgbox','assert_ndless_rev',
                     'get_documents_dir','nl_hwtype','nl_hwsubtype','keypad_type','touchpad_read','touchpad_write'):
            addr=self.syms[name]; self.mu.hook_add(UC_HOOK_CODE,self.service,name,begin=addr,end=addr)

    def timer_read(self,mu,access,address,size,value,base):
        # Both SP804 counters share the clock selector at offset 0x80. Timestamps
        # and physical holds use virtual wall time, independent of poll count.
        self.us+=50
        speed=struct.unpack('<I',mu.mem_read(base+0x80,4))[0]
        hz=32768 if speed&2 else 12000000 if speed&1 else 99000000
        control=struct.unpack('<I',mu.mem_read(address+4,4))[0]
        running=bool(control&0x80) and not self.stalled
        ticks=self.us*hz//1000000 if running else 0
        self.timer_reads.add(address)
        mu.mem_write(address,struct.pack('<I',(0xffffffff-ticks)&0xffffffff))

    def advance_inputs(self,mu,address,size,_):
        # No hook replaces device_poll, device_sleep, touchpad_scan or on_key_pressed.
        # The assembly actually reads the keypad matrix and polled timer registers.
        self.us+=500
        while self.us>=self.hold_until:
            action=next(self.keys)
            if isinstance(action,tuple) and action[0]=='snap':
                self.snapshots[action[1]]=self.screen;continue
            if isinstance(action,tuple) and action[0]=='elapsed_ms':
                self.us+=action[1]*1000;continue
            if isinstance(action,tuple) and action[0]=='for_ms':
                _,duration,action=action
                self.hold_until=self.us+1000*duration;self.hold_action=action
            break
        else:
            action=self.hold_action
        self.native_polls+=1
        if action=='cut':self.cut=True;mu.emu_stop();return
        rows=[0]*8;self.touch=None;self.touch_error=False
        table={'esc':(0x1c,0x80),'enter':(0x10,2),'menu':(0x1c,0x20),'books':(0x18,0x20),
               'home':(0x10,0x200),'spread':(0x18,4),'order':(0x14,1),'wide':(0x12,2),
               'pair':(0x14,4),'zoom':(0x10,0x20),'save':(0x12,0x40),'tab':(0x1c,0x200),
               'next':(0x10,0x10),'prev':(0x1a,0x200),'plus':(0x1c,4),'minus':(0x1a,4),
               'other':(0x18,0x40),'number2':(0x1c,0x10),'number8':(0x1c,0x40),
               'multiply':(0x18,0x100),'divide':(0x16,0x100)}
        arrows={'center':(160,120),'up':(160,15),'down':(160,220),'left':(15,120),'right':(299,120)}
        name = action[0] if isinstance(action,tuple) else action
        if name.startswith('ctrl'):
            rows[7] |= 0x200
            name = name[4:] or 'none'
            action = (name,*action[1:]) if isinstance(action,tuple) else name
        if isinstance(action,tuple):
            key,x,y=action
            assert key in ('touch','click')
            self.touch=(x*4,(240-y)*4,key=='click',True)
        elif action in table:
            row,mask=table[action];rows[(row-0x10)//2]|=mask
        elif action in arrows:
            x,y=arrows[action];self.touch=(x*4,(240-y)*4,1,True)
        elif action=='switch':self.touch=(0,0,1,False)
        elif action=='scan_error':self.touch_error=True
        else:assert action in ('none','power'),action
        mu.mem_write(0x900e0010,struct.pack('<8H',*rows))
        mu.mem_write(0x90140810,struct.pack('<I',0 if action=='power' else 0x100))
        mu.mem_write(0x900b0028,struct.pack('<I',0 if action=='power' else 16))

    def local(self,path):
        root='/documents/folio';assert path==root or path.startswith(root+'/'),path
        return self.folder/path[len(root):].lstrip('/')

    def cstr(self,p):
        data=bytearray()
        while True:
            chunk=bytes(self.mu.mem_read(p,32)); part,sep,_=chunk.partition(b'\x00'); data.extend(part)
            if sep: return bytes(data).decode()
            p+=32
            if len(data)>2048: raise ValueError('Unterminated string')

    def alloc(self,n):
        n=max(8,(n+7)&~7)
        for i,(at,size) in enumerate(self.heap):
            if size>=n:
                self.heap[i:i+1]=[(at+n,size-n)] if size>n else []; self.sizes[at]=n; return at
        raise MemoryError('Test allocator exhausted')

    def release(self,p):
        if not p: return
        self.heap.append((p,self.sizes.pop(p))); self.heap.sort()
        merged=[]
        for start,size in self.heap:
            if merged and sum(merged[-1])==start: merged[-1]=(merged[-1][0],merged[-1][1]+size)
            else: merged.append((start,size))
        self.heap=merged

    def handle(self,f):
        self.slot+=4; self.handles[self.slot]=f; return self.slot

    def service(self,mu,address,size,name):
        a,b,c,d=(mu.reg_read(r) for r in REGS); result=0
        if name=='malloc': result=self.alloc(a)
        elif name=='calloc': result=self.alloc(a*b); mu.mem_write(result,bytes(a*b))
        elif name=='free': self.release(a)
        elif name=='realloc' and (self.realloc_limit is None or b<=self.realloc_limit):
            result=self.alloc(b)
            if a: mu.mem_write(result,bytes(mu.mem_read(a,min(self.sizes[a],b)))); self.release(a)
        elif name=='fopen':
            path=self.local(self.cstr(a)); mode=self.cstr(b)
            try: result=self.handle(path.open(mode))
            except OSError: pass
        elif name=='fclose': self.handles.pop(a).close()
        elif name=='fread':
            raw=self.handles[d].read(b*c); mu.mem_write(a,raw); result=len(raw)//b if b else 0; self.io_reads+=len(raw)
        elif name=='fwrite':
            result=self.handles[d].write(bytes(mu.mem_read(a,b*c)))//b if b else 0
        elif name=='fflush': self.handles[a].flush()
        elif name=='fseek': self.handles[a].seek(b if b<2**31 else b-2**32,c)
        elif name=='ftell': result=self.handles[a].tell()
        elif name=='nuc_opendir':
            path=self.local(self.cstr(a))
            if path.is_dir(): result=self.handle(iter(sorted(p.name for p in path.iterdir())))
        elif name=='nuc_readdir':
            item=next(self.handles[a],None)
            if item: mu.mem_write(self.entrybuf,item.encode()+b'\0'); result=self.entrybuf
        elif name=='nuc_closedir': self.handles.pop(a)
        elif name=='lcd_blit': self.screen=bytes(mu.mem_read(a,320*240*2))
        elif name=='lcd_init': result=1
        elif name=='device_message': self.messages.append(self.cstr(a))
        elif name=='_show_msgbox': self.messages.append(self.cstr(b))
        elif name=='nl_hwtype': result=1
        elif name=='nl_hwsubtype': result=self.subtype
        elif name=='keypad_type': result=self.kptype
        elif name=='get_documents_dir': result=self.docs
        elif name=='touchpad_write': self.page=mu.mem_read(c,1)[0]; result=1
        elif name=='touchpad_read':
            ok=not self.touch_error
            if self.page==0x10 and (a,b)==(4,7):
                self.geometry_reads+=1
                ok=self.geometry_reads>self.geometry_failures
                data=struct.pack('>HH',1280,960)
            elif self.page==4 and (a,b)==(0,10):
                x,y,pressed,contact=self.touch or (0,0,0,False)
                data=struct.pack('>BBHHBBHB',int(contact),0,x,y,0,0,0,pressed)
            else:raise AssertionError((self.page,a,b))
            mu.mem_write(c,data);result=int(ok)
        mu.reg_write(UC_ARM_REG_R0,result&0xffffffff); mu.reg_write(UC_ARM_REG_PC,mu.reg_read(UC_ARM_REG_LR))

    def run(self,location="/documents/folio/Folio.tns",expected=0):
        argv=self.alloc(8); path=self.alloc(64); self.mu.mem_write(path,location.encode()+b'\0'); self.mu.mem_write(argv,struct.pack('<II',path,0))
        self.mu.reg_write(UC_ARM_REG_R0,1); self.mu.reg_write(UC_ARM_REG_R1,argv); self.mu.reg_write(UC_ARM_REG_LR,STOP); self.mu.reg_write(UC_ARM_REG_SP,STACK+0x3ffe0)
        try:
            self.mu.emu_start(self.syms['main'],STOP,timeout=0,count=250000000)
            assert self.cut or (self.mu.reg_read(UC_ARM_REG_PC)==STOP and self.mu.reg_read(UC_ARM_REG_R0)==expected), (self.messages,hex(self.mu.reg_read(UC_ARM_REG_PC)))
            if not expected: assert not self.messages,self.messages
            if not self.cut:
                for address,value in self.initial_registers.items():
                    assert bytes(self.mu.mem_read(address,4))==struct.pack('<I',value),hex(address)
        except UcError as exc:
            pc=self.mu.reg_read(UC_ARM_REG_PC)
            symbol=max((v,k) for k,v in self.syms.items() if v<=pc)
            raise AssertionError((hex(pc),symbol,self.native_polls,self.messages)) from exc
        finally:
            for h in self.handles.values():
                if hasattr(h,'close'): h.close()
        return self

def timed_centre(folder):
    # Keep the finger resting on the surface between mechanical clicks.
    rest=('touch',160,120)
    actions=['none',('snap','fit'),('for_ms',100,'center'),('for_ms',500,rest),('snap','one'),
             ('for_ms',95,'center'),('for_ms',155,rest),('for_ms',90,'center'),
             ('snap','two'),('for_ms',520,rest),('snap','settled'),
             ('for_ms',850,'center'),('for_ms',500,rest),('snap','long'),
             ('for_ms',130,'none'),'switch',('for_ms',515,'none'),('snap','switch-only'),
             ('for_ms',65,('click',168,125)),'scan_error','scan_error',('for_ms',80,('click',168,125)),
             ('for_ms',500,rest),('snap','interrupted'),
             ('touch',290,120),'switch',('for_ms',90,'center'),('for_ms',500,rest),('snap','late-position'),
             ('for_ms',80,'center'),('for_ms',80,rest),('elapsed_ms',600),
             ('for_ms',80,'center'),('for_ms',500,rest),('snap','separate'),'power']
    return Machine(folder,actions).run()

def check_pad_positions(folder,book):
    from verify import render
    folder.mkdir()
    shutil.copyfile(book,folder/book.name)
    release=('for_ms',90,'none')
    settle=('for_ms',560,'none')
    actions=['none',('snap','start'),('touch',160,120),('for_ms',150,'switch'),
             ('touch',160,120),settle,('snap','blind'),
             ('touch',200,120),'switch','none',settle,('snap','old-direction'),
             ('click',0,240),('click',325,120),('touch',160,120),settle,('snap','bad-position'),
             ('touch',160,120),('for_ms',150,'switch'),('click',200,120),('snap','late-right'),
             ('for_ms',200,'center'),'none',('for_ms',210,'center'),settle,('snap','held'),
             ('click',126,120),release,('snap','left'),
             ('click',160,146),release,('snap','down'),('click',160,94),release,('snap','up'),
             ('click',185,139),release,('snap','diagonal'),('click',160,94),release,
             'center',('click',200,120),settle,('snap','outward'),
             ('click',126,120),release,
             'center',('touch',200,120),settle,('snap','outward-release'),
             ('click',126,120),release,
             'center','switch','none',settle,('snap','lost-centre'),
             'center','none',settle,('snap','short-centre'),'power']
    result=Machine(folder,actions,geometry_failures=1).run()
    assert result.geometry_reads==2
    s=result.snapshots
    first=render(book,0)[0]
    second=render(book,480)[0]
    assert s['start']==s['blind']==s['old-direction']==s['bad-position']==s['lost-centre']==first
    assert s['late-right']==s['held']==s['outward']==s['outward-release']==second
    assert s['left']==s['up']==first and s['down']==s['diagonal']==render(book,6)[0]
    assert s['short-centre']==render(book,0,1)[0]

def check_chapter_menu(folder):
    from verify import meta,pack,render
    folder.mkdir()
    book=folder/'PlatformSix.folio.tns'
    shutil.copyfile(ROOT/'folio'/'Light Novels'/book.name,book)
    lift=('for_ms',80,'none')
    actions=[('for_ms',200,'center'),('for_ms',600,'none'),('snap','open'),
             'menu',lift,('snap','commands'),'enter',lift,('snap','chapters'),
             ('touch',160,120),('for_ms',120,'switch'),lift,('snap','unlocated'),
             ('for_ms',500,('click',160,150)),lift,('snap','highlighted'),
             ('for_ms',650,'menu'),'none','menu',('snap','parent'),lift,'menu',lift,('snap','reading'),
             'menu',lift,'enter',lift,'down',lift,'center',lift,('snap','chosen'),'power']
    s=Machine(folder,actions).run().snapshots
    assert s['open']==s['reading']==render(book)[0]
    assert s['commands']==s['parent']!=s['chapters']
    assert s['unlocated']==s['chapters']!=s['highlighted']
    entry_size=8+pack.LABEL_W*pack.LABEL_H//8
    chapter_y=struct.unpack_from('<I',book.read_bytes(),meta(book)[6]+entry_size)[0]
    assert s['chosen']==render(book,chapter_y)[0]!=s['open']
    assert Machine(folder,['none','power']).run().screen==s['chosen']

def check_trim_keys(folder,book):
    from verify import meta,render
    folder.mkdir()
    copy=folder/book.name
    shutil.copyfile(book,copy)
    first,second=(n & 0x7fffffff for n in struct.unpack_from('<2I',copy.read_bytes(),meta(copy)[7]))
    assert first==0 and second>=240
    lift=('for_ms',90,'none')
    rest=('for_ms',1250,'none')
    actions=['none',('snap','fit'),('for_ms',700,'multiply'),('snap','notice'),
             'none',('for_ms',200,'multiply'),rest,('snap','once'),'multiply',lift,
             'multiply',lift,'divide',rest,('snap','twice'),'right',lift,('snap','next'),
             'center',rest,('snap','detail'),'center',rest,('snap','back'),'power']
    s=Machine(folder,actions).run().snapshots
    assert s['fit']==render(copy)[0] and s['once']==render(copy,6,trim=1)[0]
    assert s['notice']!=s['once'] and s['notice'][20*640:]==s['once'][20*640:]
    assert s['twice']==render(copy,11,trim=2)[0]
    assert s['next']==s['back']==render(copy,second+11,trim=2)[0]
    assert s['detail']==render(copy,2*second+80,1,106)[0]
    assert Machine(folder,['none','power']).run().screen==s['back']
    tight=Machine(folder,['none','power'])
    tight.realloc_limit=5*64*640*2
    assert tight.run().screen==s['back']


if __name__=='__main__':
    from verify_navigation import navigation_pdf
    from verify_spreads import leaf_source,frame
    from verify_appearance import zoomed_fold
    from verify import prepare,render
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);folder=root/'folio';folder.mkdir()
        bad=Machine(folder,[]).run('/documents/sub/folio/Folio.tns',expected=1)
        assert any('top-level' in m for m in bad.messages)
        source,art=leaf_source(root);book=folder/'A.folio.tns';prepare(source,book)
        expected=render(book,0,1)[0]
        timing=root/'gestures-by-time';timing.mkdir();shutil.copyfile(book,timing/book.name)
        machine=timed_centre(timing)
        screens=machine.snapshots
        assert screens['one']==expected and screens['fit']==render(book,0)[0]
        assert screens['two']==screens['settled']==screens['interrupted']==frame(art)
        assert screens['long']==screens['switch-only']==screens['late-position']==screens['separate']==zoomed_fold(art)
        assert machine.timer_reads=={0x900d0004}
        for registers,selected in (({0x900d0008:0x82},0x900d0024),
                                   ({0x900d0008:0x82,0x900d0028:0x82,0x90010008:0x82},0x900c0004)):
            unused=Machine(timing,['none','power'],timer_registers=registers).run()
            assert unused.timer_reads=={selected}
        busy={base+offset:0x82 for base in (0x90010000,0x900c0000,0x900d0000) for offset in (8,0x28)}
        assert not Machine(timing,['none','power'],timer_registers=busy).run().timer_reads
        check_pad_positions(root/'pad-positions',book)
        check_chapter_menu(root/'chapter-menu')
        check_trim_keys(root/'trim-keys',book)
        print('PASS physical multiply and divide keys, held-key filtering, centred trim, 1.5x toggle and cold resume')
        print('PASS inner arrows, centre confirmation, unlocated and late presses, geometry retry, timed gestures, chapter Menu return and shared timers')
        # The inner directional zone starts near the centre. Sliding a held
        # click through the middle and one noisy release cannot turn twice.
        sequence=['none',('snap','start'),('touch',80,80),('touch',180,120),('snap','bare'),
                  'ctrl',('snap','cursor'),'none',('snap','hidden'),('click',217,120)]
        sequence += [('click',160,120)]*35 + [('snap','held'),'none',('click',217,120),('snap','bounce'),
                    'none','none',('click',217,120),('snap','next'),'none', 'ctrlcenter']
        sequence += ['none']*52 + [('snap','continuous'),'power']
        zones=Machine(folder,sequence).run().snapshots
        assert zones['start']==zones['bare']==zones['hidden'] and zones['cursor']!=zones['hidden']
        assert zones['held']==zones['bounce']==render(book,480)[0]
        assert zones['next']==zones['continuous']==render(book,960)[0]
        # Reset the fixture's reading position and mode with physical controls.
        Machine(folder,['none','ctrlcenter','none','minus','none','minus','none','power']).run()
        keys=['none','center','none']+['none']*24+[('snap','zoom'),'number2','none','number8','none','other']
        keys+=['none','center','none','none','center','none',('snap','spread'),'order','none','pair','none','wide','none','tab','none','save']
        keys+=['none']*60+['center']*34+['none','menu','none',('snap','menu'),'down','none','none','center','none',('snap','shelf'),'esc']
        warm=Machine(folder,keys).run()
        assert warm.snapshots['zoom']==expected and warm.snapshots['spread']==frame(art)
        assert warm.snapshots['menu']!=warm.snapshots['shelf'] and warm.native_polls>120
        # A key is held across menu entry and release. All controls still poll.
        cold=Machine(folder,['none',('snap','cold'),'spread','none','zoom','none','next','none','prev','none','plus','none','minus','none','menu','none','enter','none','esc','none','home','none','enter','none','power']).run()
        assert cold.native_polls>=20
        # No timer or keypad interrupt is delivered, and this timer never ticks.
        stuck=Machine(folder,['none','other','none','esc','none','esc'],stalled=True).run()
        assert stuck.native_polls==6
        cx=Machine(folder,['none','other','none','power'],subtype=1).run()
        assert cx.native_polls==4
        print('PASS ARM keypad and touchpad, Ctrl cursor, inner direction zones, press latch and bounce, stalled clock, Menu, single/double/held centre and RGB565 spread')
        pdf=root/'links.pdf';navigation_pdf(pdf)
        section=folder/'PDF';section.mkdir();linked=section/'A.folio.tns';prepare(pdf,linked)
        # Cold opens the former book. Home shows root with the folder ahead of the book.
        lift=('for_ms',75,'none')
        actions=['none','home','none','up',lift,'center',lift,'center',lift,('ctrltouch',100,100),('ctrltouch',104,100),'ctrlcenter']
        actions+=['none']*18+[('snap','jump'),'menu',lift,('snap','reader-menu'),'center',lift,'down',lift,
                              ('for_ms',600,'menu'),'none','menu',('snap','parent'),lift,'menu',lift,('snap','still-reading'),
                              'menu',lift,'center',lift,'down',lift,'down',lift,'center',lift,('snap','bookmark'),
                              'menu',lift,'enter',lift,'enter',lift,('snap','contents-page'),
                              ('ctrlclick',202,120),lift,('snap','pointer-click'),
                              'esc','none','esc','none','esc']
        pdf_run=Machine(folder,actions).run()
        assert pdf_run.snapshots['jump']==pdf_run.snapshots['bookmark']==render(linked,645)[0]
        assert pdf_run.snapshots['reader-menu']==pdf_run.snapshots['parent']!=pdf_run.snapshots['still-reading']
        assert pdf_run.snapshots['jump']==pdf_run.snapshots['still-reading']
        assert pdf_run.snapshots['contents-page']==render(linked,0)[0]
        assert pdf_run.snapshots['pointer-click']==pdf_run.snapshots['jump']
        linked.rename(folder/'moved.folio.tns')
        restarted=Machine(folder,['none',('snap','resumed'),'power']).run()
        assert restarted.snapshots['resumed']==pdf_run.snapshots['jump']
        print('PASS physical clicks on a PDF, bookmark Menu return, nested outline selection, folder shelves and cold resume after moving a title')
