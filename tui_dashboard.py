import os, sys, time, subprocess, threading, requests, psutil
from datetime import datetime, timedelta
from collections import deque
from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.live import Live
from rich.text import Text
from rich.align import Align
from rich import box

FLASK_URL  = "http://127.0.0.1:5050/api/lane_counts"
LOG_MAX    = 200
NOISE      = ["/api/lane_counts", "/api/intensity", "HTTP/1.1", "GET /static", "GET /video_feed"]
START_TIME = time.time()
console    = Console()
logs       = deque(maxlen=LOG_MAX)
seen       = deque(maxlen=80)

def _noisy(l): return any(n in l for n in NOISE)
def _color(l):
    for tag, c in [("[AUTO]","bold green"),("[ERR]","bold red"),("error","red"),
                   ("[WARN]","yellow"),("[INIT]","bold cyan"),("[MODE2]","blue"),
                   ("[HMI]","bold white"),("[EXT]","magenta")]:
        if tag.lower() in l.lower(): return f"[{c}]{l}[/]"
    return l

class Dashboard:
    def __init__(self):
        self.data={}; self.hw={}; self.proc=None; self.alive=True; self.errs=0

    def launch(self):
        env=os.environ.copy(); env["PYTHONUNBUFFERED"]="1"
        self.proc=subprocess.Popen([sys.executable,"app.py"],
            stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,env=env)
        def _r():
            for raw in iter(self.proc.stdout.readline,""):
                if not self.alive: break
                l=raw.strip()
                if not l or _noisy(l) or l in seen: continue
                seen.append(l); logs.append(_color(l))
        threading.Thread(target=_r,daemon=True).start()

    def poll(self):
        try:
            r=requests.get(FLASK_URL,timeout=0.8)
            if r.ok: self.data=r.json(); self.errs=0; return
        except: pass
        self.errs+=1
        if self.errs>=5: self.data={}

    def poll_hw(self):
        self.hw={"cpu":psutil.cpu_percent(None),"ram":psutil.virtual_memory().percent,
                 "disk":psutil.disk_usage("/").percent}

    def hdr(self):
        up=str(timedelta(seconds=int(time.time()-START_TIME)))
        ok=self.errs<5
        g=Table.grid(expand=True)
        g.add_column(ratio=1); g.add_column(justify="center",ratio=1); g.add_column(justify="right",ratio=1)
        g.add_row(
            f"[bold magenta]STMCV AI TRAFFIC[/]  " + ("[green]●API[/]" if ok else "[red]●DOWN[/]"),
            "[bold white]DASHBOARD v3.0[/]",
            f"[cyan]{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/]  [dim]UP {up}[/]"
        )
        return Panel(g, style="white on blue")

    def traffic_panel(self):
        d=self.data
        counts=d.get("counts",{}); tls=d.get("tl_states",{}); m2=d.get("mode2_counts",{})
        green=d.get("green_lane"); timer=d.get("timer",0)
        started=d.get("system_started",False); mode=d.get("control_mode",1)
        t=Table(expand=True,box=box.SIMPLE_HEAVY,header_style="bold cyan")
        t.add_column("Lane",width=8,style="bold white")
        t.add_column("Veh",width=5,justify="right",style="yellow")
        t.add_column("Signal",width=10,justify="center")
        for lane in ["North","South","East","West"]:
            cnt=m2.get(lane,counts.get(lane,0))
            state=tls.get(lane,"red").upper()
            sig={"GREEN":"[bold green]▶ GREEN[/]","YELLOW":"[bold yellow]◆ YIELD[/]"}.get(state,"[red]● RED[/]")
            rs="on dark_green" if state=="GREEN" else ""
            t.add_row(lane,str(cnt),sig,style=rs)
        c=Table.grid(expand=True)
        c.add_row(t); c.add_row("")
        c.add_row(Text.assemble(("Green Lane: ","white"),(f"{green or 'NONE':8}","bold green" if green else "bold red"),("  ~","dim"),( f"{timer}s","bold yellow")))
        c.add_row(Text.assemble(("Algorithm: ","white"),("DENSITY-WEIGHT" if mode==2 else "FIXED CYCLE","bold cyan")))
        c.add_row(Text.assemble(("System:    ","white"),("RUNNING" if started else "STOPPED","bold green" if started else "bold red")))
        return Panel(c, title="[bold cyan]Traffic Intelligence[/]", border_style="cyan")

    def sys_panel(self):
        def bar(v,l):
            col="green" if v<50 else("yellow" if v<80 else"red")
            return Text.assemble((f"{l:4} ","dim white"),(f"{'█'*int(v/10)}{'░'*(10-int(v/10))}",col),(f" {v:5.1f}%","bold "+col))
        c=Table.grid(expand=True)
        c.add_row(bar(self.hw.get("cpu",0),"CPU"))
        c.add_row(bar(self.hw.get("ram",0),"RAM"))
        c.add_row(bar(self.hw.get("disk",0),"DSK"))
        c.add_row("")
        conn=self.data.get("connection","Disconnected"); cc="green" if "Connected" in conn else "red"
        c.add_row(f"Controller: [{cc}]{conn}[/]")
        det=self.data.get("detect_status","INACTIVE"); dc="green" if det=="ACTIVE" else("yellow" if det=="READY" else"dim")
        c.add_row(f"Detection:  [{dc}]{det}[/]")
        pid=self.proc.pid if self.proc else "--"
        c.add_row(f"[dim]PID: {pid}[/]")
        return Panel(c, title="[bold blue]System Health[/]", border_style="blue")

    def cam_panel(self):
        h=self.data.get("mode2_thread_health",{}); m=self.data.get("mode2_counts",{})
        t=Table(expand=True,box=None,show_header=False)
        t.add_column("D",width=2,style="cyan"); t.add_column("S",width=8,justify="center"); t.add_column("C",width=4,justify="right",style="yellow")
        for lane in ["North","South","East","West"]:
            alive=h.get(lane, lane in m)
            t.add_row(lane[0],"[green]LIVE[/]" if alive else "[red]OFF[/]",str(m.get(lane,0)))
        return Panel(t, title="[bold magenta]Cams[/]", border_style="magenta")

    def log_panel(self):
        lines=list(logs)[-30:]
        return Panel("\n".join(lines) if lines else "[dim]no logs[/]", title="[bold white]Live Logs[/]", border_style="white")

    def ftr(self):
        pid=self.proc.pid if self.proc else "?"
        return Panel(Align.center(f"[bold yellow]Ctrl+C[/] Exit | [cyan]API :5050[/] | [magenta]Web http://localhost:5050[/] | [dim]PID {pid}[/]"), style="white on blue")

    def run(self):
        self.launch()
        layout=Layout()
        layout.split(Layout(name="h",size=3),Layout(name="body",ratio=1),Layout(name="f",size=3))
        layout["body"].split_row(Layout(name="left",ratio=1),Layout(name="right",ratio=2))
        layout["left"].split_column(Layout(name="trf",ratio=3),Layout(name="sys",ratio=3),Layout(name="cam",ratio=2))
        with Live(layout,refresh_per_second=4,screen=True):
            try:
                while True:
                    self.poll(); self.poll_hw()
                    layout["h"].update(self.hdr())
                    layout["trf"].update(self.traffic_panel())
                    layout["sys"].update(self.sys_panel())
                    layout["cam"].update(self.cam_panel())
                    layout["right"].update(self.log_panel())
                    layout["f"].update(self.ftr())
                    time.sleep(0.5)
            except KeyboardInterrupt:
                self.alive=False
                if self.proc: self.proc.terminate()
                console.print("[bold red]Shutting down...[/]")

if __name__=="__main__":
    Dashboard().run()
