import tkinter as tk
from tkinter import ttk
import serial, serial.tools.list_ports
import threading, time

COLS, ROWS = 32, 8
PX = 28  # pixel size in UI — bigger since the canvas is small now

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("8×32 LED Painter")
        self.pixels = [[(0,0,0)]*COLS for _ in range(ROWS)]
        self.color = (255, 0, 0)
        self.ser = None
        self.dirty = False
        self._build_ui()
        threading.Thread(target=self._sender, daemon=True).start()

    def _build_ui(self):
        top = tk.Frame(self.root)
        top.pack(fill=tk.X, padx=6, pady=4)

        tk.Label(top, text="Port:").pack(side=tk.LEFT)
        self.port_var = tk.StringVar()
        ports = [p.device for p in serial.tools.list_ports.comports()]
        cb = ttk.Combobox(top, textvariable=self.port_var, values=ports, width=12)
        if ports: cb.current(0)
        cb.pack(side=tk.LEFT, padx=4)

        self.btn = tk.Button(top, text="Connect", command=self._toggle)
        self.btn.pack(side=tk.LEFT)

        self.lbl = tk.Label(top, text="●  not connected", fg="gray")
        self.lbl.pack(side=tk.LEFT, padx=8)

        palette = [
            ("■", (255,0,0)),("■",(0,255,0)),("■",(0,0,255)),
            ("■",(255,255,0)),("■",(0,255,255)),("■",(255,0,255)),
            ("■",(255,140,0)),("■",(255,255,255)),("■",(0,0,0)),
        ]
        for sym, rgb in palette:
            h = "#{:02x}{:02x}{:02x}".format(*rgb)
            bg = h if rgb != (0,0,0) else "#222"
            tk.Button(top, text=sym, bg=bg, fg="white", width=2,
                      relief=tk.FLAT,
                      command=lambda c=rgb: self._pick(c)).pack(side=tk.LEFT, padx=1)

        tk.Button(top, text="Clear", command=self._clear).pack(side=tk.LEFT, padx=6)

        self.cv = tk.Canvas(self.root, width=COLS*PX, height=ROWS*PX,
                            bg="#0a0a0a", cursor="crosshair")
        self.cv.pack(padx=6, pady=6)

        self.rects = {}
        for r in range(ROWS):
            for c in range(COLS):
                x0, y0 = c*PX+1, r*PX+1
                rect = self.cv.create_rectangle(x0, y0, x0+PX-2, y0+PX-2,
                                                fill="#0a0a0a", outline="")
                self.rects[(c,r)] = rect

        # Grid lines
        for c in range(COLS+1):
            self.cv.create_line(c*PX, 0, c*PX, ROWS*PX, fill="#1a1a1a")
        for r in range(ROWS+1):
            self.cv.create_line(0, r*PX, COLS*PX, r*PX, fill="#1a1a1a")

        self.cv.bind("<Button-1>", self._click)
        self.cv.bind("<B1-Motion>", self._click)
        self.cv.bind("<Button-3>", self._erase)
        self.cv.bind("<B3-Motion>", self._erase)

    def _pos(self, e):
        c, r = e.x // PX, e.y // PX
        return (c, r) if 0 <= c < COLS and 0 <= r < ROWS else None

    def _paint(self, c, r, col):
        self.pixels[r][c] = col
        self.cv.itemconfig(self.rects[(c,r)],
                           fill="#{:02x}{:02x}{:02x}".format(*col))
        self.dirty = True

    def _click(self, e):
        p = self._pos(e)
        if p: self._paint(*p, self.color)

    def _erase(self, e):
        p = self._pos(e)
        if p: self._paint(*p, (0,0,0))

    def _pick(self, c): self.color = c
    def _clear(self):
        for r in range(ROWS):
            for c in range(COLS): self._paint(c, r, (0,0,0))

    def _toggle(self):
        if self.ser and self.ser.is_open:
            self.ser.close(); self.ser = None
            self.lbl.config(text="●  not connected", fg="gray")
            self.btn.config(text="Connect")
        else:
            try:
                self.ser = serial.Serial(self.port_var.get(), 500000, timeout=0.5)
                time.sleep(2)
                self.lbl.config(text="●  connected", fg="green")
                self.btn.config(text="Disconnect")
            except Exception as ex:
                self.lbl.config(text=f"✗  {ex}", fg="red")

    def _sender(self):
        while True:
            time.sleep(1/30)
            if self.ser and self.ser.is_open and self.dirty:
                self.dirty = False
                pkt = bytearray([0xFF, 0xFE])
                for r in range(ROWS):
                    for c in range(COLS):
                        pkt.extend(self.pixels[r][c])
                try:
                    self.ser.write(pkt)
                    self.ser.read(1)   # wait for ACK 'K'
                except:
                    self.ser = None

if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()