"""Measure hover I/O and exercise the spread controls in the packaged ARM launcher."""
from pathlib import Path
import shutil
import tempfile

from arm_smoke import Machine
import verify as v
from verify_appearance import pixel, press, zoomed_fold
from verify_spreads import frame, leaf_source


class TracedMachine(Machine):
    def __init__(self, folder, commands):
        super().__init__(folder, commands)
        self.opens = 0
        self.read_calls = 0
        self.samples = {}
        class Snapshots(dict):
            def __setitem__(snapshots, name, screen):
                self.samples[name] = (self.opens, self.read_calls, self.io_reads)
                super().__setitem__(name, screen)
        self.snapshots = Snapshots()

    def service(self, mu, address, size, name):
        if name=='fopen':
            self.opens += 1
        elif name=='fread':
            self.read_calls += 1
        super().service(mu,address,size,name)


def sweep():
    row1 = ('ctrltouch',150,100)
    row2 = ('ctrltouch',150,150)
    return ['ctrl',('ctrltouch',150,200),('snap','start'),
            row1,('snap','second'),row2,('snap','third'),row1,('snap','back'),
            ('for_ms',280,row1),('snap','idle'),row2,row1,('for_ms',280,row1),
            ('snap','cached'),'none',('snap','no-cursor'),'power']


def check_sweep(machine, shelf):
    io = machine.samples
    assert io['start']==io['second']==io['third']==io['back'],io
    assert io['idle']==io['cached']==io['no-cursor'],io
    if shelf:
        assert io['idle'][2]>io['start'][2],io
    else:
        assert io['idle']==io['start'],io
    s = machine.snapshots
    blue = 0x1b4d
    assert pixel(s['second'],8,69)==pixel(s['third'],8,94)==blue
    assert pixel(s['second'],8,94)!=blue and pixel(s['third'],8,69)!=blue
    assert s['second']==s['back'] and s['idle']==s['cached']
    # Hiding Ctrl changes only the saved pointer footprint.
    changed = [(i%320,i//320) for i in range(320*240)
               if s['idle'][2*i:2*i+2]!=s['no-cursor'][2*i:2*i+2]]
    assert changed and max(x for x,y in changed)-min(x for x,y in changed)<5
    assert max(y for x,y in changed)-min(y for x,y in changed)<13


def check_hover(root):
    shelf = root/'shelf'
    shelf.mkdir()
    for i,source in enumerate(sorted((v.ROOT/'folio').rglob('*.folio.tns'))):
        shutil.copyfile(source,shelf/f'{i}.folio.tns')
    v.run(shelf,press('menu','enter','enter','esc')+'power')
    machine = TracedMachine(shelf,['none',*sweep()]).run()
    check_sweep(machine,True)
    delta = tuple(b-a for a,b in zip(machine.samples['start'],machine.samples['idle']))
    print(f'PASS ARM shelf crosses three rows with zero opens/reads; one settled preview uses {delta[0]} opens, {delta[1]} reads, {delta[2]} bytes; cached revisit uses zero')
    contents = root/'contents'
    contents.mkdir()
    shutil.copyfile(v.ROOT/'folio/PDF/StationAtlas.folio.tns',contents/'Atlas.folio.tns')
    lift = ('for_ms',90,'none')
    machine = TracedMachine(contents,['none','menu',lift,'enter',lift,*sweep()]).run()
    check_sweep(machine,False)
    print('PASS ARM bookmark labels stay cached and releasing Ctrl restores the pixels under the pointer')


def check_spread(root):
    source,art = leaf_source(root)
    folder = root/'spread'
    folder.mkdir()
    book = folder/'Pages.folio.tns'
    v.prepare(source,book)
    lift = ('for_ms',90,'none')
    settle = ('for_ms',550,'none')
    double = ['center',lift,'center',settle]
    actions = ['none','multiply',lift,'multiply',lift,'multiply',lift,'multiply',lift,
               *double,('snap','fit'),'center',settle,('snap','detail'),'right',lift,'down',lift,
               ('snap','pan'),('for_ms',850,'none'),'cut']
    s = Machine(folder,actions).run().snapshots
    assert s['fit']==frame(art)
    assert s['detail']==zoomed_fold(art) and s['pan']==zoomed_fold(art,12,12)
    cold = Machine(folder,['none',('snap','resumed'),*double,('snap','single'),'power']).run()
    assert cold.snapshots['resumed']==s['pan'] and cold.snapshots['single']==v.render(book,20,trim=4)[0]
    print('PASS ARM physical centre gestures, joined 1.5x zoom, two-axis pan, interrupted restart and restored single-page trim')


if __name__=='__main__':
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        check_hover(root)
        check_spread(root)
