"""Create three small, original books through the release converter."""
from pathlib import Path
import math
import sys
import tempfile
from PIL import Image, ImageDraw, ImageFont
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pack

NOVEL='''# Platform Six

At eleven minutes past midnight, the station clock lost its minute hand. Nell was the only passenger left to notice. She shut her paperback around her ticket and went to look under the bench.

A man in a paint-spotted jacket sat there with his arm buried to the elbow in a toolbox. He was drawing a straight black line down a long piece of brass.

“You're early,” he said.

“For the last train?”

“For tomorrow.” He turned the brass over. There was a round hole in one end and a little counterweight on the other. Nell looked at the clock. The man looked at the ticket sticking out of her book.

“I can lend you a bookmark,” he said, “if you can lend me the time.”

She checked her watch. Eleven minutes past midnight. She checked it again. The seconds were moving, but the minute was still eleven.

# The Return Ticket

The man climbed a ladder and fitted the hand over the clock's square spindle. From the platform below it looked no thicker than the stroke of a pen.

“Anywhere special?” he called.

Nell opened her book. Her father had used the back of the ticket to draw a map. It had one platform, one shelter and a small arrow pointing to a bench. She had spent most of the evening wondering which station he meant.

She sat down. From here the clock, the ladder and the man lined up exactly with three marks she had taken for trees. She tipped the ticket over. A pencilled sentence caught the light.

Wait until the hand reaches twelve.

Overhead, there was a click. Across the tracks a shutter rolled up on a tiny bakery. The smell of hot bread crossed the platform before the sound did. In the doorway stood a woman holding up a paper bag with Nell's name on it.

Inside were two buns. Her father had always bought one more than he needed. For the first time that evening Nell knew exactly where she was.
'''

def crossing(source, out, profile):
    # One drawing split at its exact centre. The earlier manga page is the right half.
    art=Image.new('RGB',(640,480),(226,205,174)); d=ImageDraw.Draw(art)
    dark=(32,47,63); distant=(129,159,156); red=(174,73,57); white=(254,240,203)
    d.ellipse((438,25,535,122),fill=white)
    d.polygon([(0,185),(85,118),(119,149),(203,100),(365,183),(455,129),(520,158),(590,103),(640,149),(640,252),(0,252)],fill=distant)
    d.rectangle((0,251,639,479),fill=(92,139,148))
    for x in range(-40,680,92):
        d.polygon([(x,254),(x+17,254),(x+44,402),(x+33,402)], fill=dark)
        d.line((x,273,x+95,323),fill=dark,width=6)
        d.line((x+95,273,x+25,323),fill=dark,width=6)
    d.rectangle((0,241,639,272),fill=dark)
    d.line((0,254,639,254),fill=white,width=2)
    for x in (114,222,330,438):
        d.rounded_rectangle((x,179,x+103,240),radius=9,fill=red,outline=dark,width=3)
        d.rectangle((x+4,199,x+99,222),fill=white)
        for w in range(x+10,x+92,19): d.rectangle((w,203,w+11,215),fill=dark)
        d.rectangle((x+6,233,x+97,239),fill=dark)
        d.ellipse((x+13,232,x+23,242),fill=dark); d.ellipse((x+79,232,x+89,242),fill=dark)
    font=ImageFont.truetype(str(pack.DEFAULT_FONT),33)
    d.text((32,27),'Twin Rails',fill=dark,font=font)
    for x,y in ((25,348),(150,399),(255,373),(358,422),(515,357),(581,414)):
        d.line((x,y,x+33,y),fill=white,width=2)
    source.mkdir()
    art.crop((320,0,640,480)).save(source/'01.png')
    art.crop((0,0,320,480)).save(source/'02.png')
    pack.main([str(source),'--compression',profile,'--title','Twin Rails','-o',str(out/'TwinRails.folio.tns')])


def create(out, profile='compact'):
    out=Path(out); out.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp)
        text=root/'Platform Six.txt'; text.write_text(NOVEL,encoding='utf-8')
        pack.main([str(text),'--compression',profile,'--title','Platform Six','-o',str(out/'PlatformSix.folio.tns')])
        ink=(26,36,68); pale=(246,238,208); coral=(229,124,101); teal=(97,180,174)
        font=ImageFont.truetype(str(pack.DEFAULT_FONT),29); tiny=ImageFont.truetype(str(pack.DEFAULT_FONT),21)
        source=root/'Night Signal'; source.mkdir()
        for chapter in range(2):
            h=1440; art=Image.new('RGB',(640,h),ink); d=ImageDraw.Draw(art)
            d.rectangle((0,0,639,h//2),fill=(32,57,85) if chapter==0 else (64,71,109))
            d.ellipse((436,65,547,176),fill=pale)
            for i in range(53):
                x=(i*163+15)%640; y=(i*239+31)%670
                d.point((x,y),fill=pale); d.point((x+1,y),fill=pale)
            skyline=520 if chapter==0 else 480
            for i in range(14):
                x=i*49-8; roof=skyline-(i*47)%115
                d.rectangle((x,roof,x+43,744),fill=ink)
                for j in range(3):
                    d.rectangle((x+7+j*11,roof+14,x+11+j*11,roof+21),fill=teal)
            # Rails continue across the three separate source images.
            pts=[(round(275+100*math.sin(y/260+chapter)),y) for y in range(580,h+15,6)]
            for offset in (0,70): d.line([(x+offset,y) for x,y in pts],fill=teal,width=5)
            for x,y in pts[::5]: d.line((x-12,y,x+82,y),fill=teal,width=4)
            d.rectangle((50,207,587,312),fill=pale)
            d.text((72,224),'The signal on the empty line' if chapter==0 else 'Across the rails, a light',font=font,fill=ink)
            d.text((72,264),'blinked twice.' if chapter==0 else 'answered.',font=font,fill=ink)
            d.rounded_rectangle((90,715,195,938),radius=20,fill=(14,26,40),outline=teal,width=6)
            for i in range(3): d.ellipse((112,737+63*i,174,797+63*i),fill=(coral if chapter==0 else pale) if i==chapter else (47,64,73))
            d.rectangle((135,937,151,1225),fill=teal)
            d.rectangle((65,1209,570,1310),fill=pale)
            d.text((88,1231),'Somebody was still awake.' if chapter==0 else 'She folded the timetable up.',font=font,fill=ink)
            d.text((91,1275),'Night Signal' if chapter==0 else 'End',font=tiny,fill=(75,84,96))
            ch=source/('01 - Outbound' if chapter==0 else '02 - Reply'); ch.mkdir()
            for i,(a,b) in enumerate(((0,458),(458,972),(972,h)),1):
                art.crop((0,a,640,b)).save(ch/f'{i}.png')
        pack.main([str(source),'--compression',profile,'--title','Night Signal','--detail','-o',str(out/'NightSignal.folio.tns')])

        crossing(root/'Twin Rails',out,profile)

if __name__=='__main__': create(*sys.argv[1:])
