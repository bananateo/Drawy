import tkinter as tk
from tkinter import filedialog
from PIL import Image, ImageDraw
import math
import serial
import serial.tools.list_ports
import threading
import time

# Config

NUM_MATRICES   = 4      # number of chained 8×32 LED matrices (1-4 supported by Arduino code), MUST MATCH ARDUINO CODE
MATRIX_HEIGHT  = 8       # rows per matrix, MUST MATCH ARDUINO CODE
BAUD_RATE      = 500000

# Derived from NUM_MATRICES — don't change these manually
GRID_COLS = 32
GRID_ROWS = 8 * NUM_MATRICES

BLOCK_SIZE = 20          # UI pixel size in the editor (px per cell)
CHUNK_SIZE = 20

root        = None
brush_color = '#000000'
is_painting = False
image       = None
draw_img    = None
current_tool = 'draw'

hue        = 0.0
saturation = 1.0
brightness = 0.5
wheel_radius = 80

ser   = None
ser_lock = threading.Lock()
dirty = False

prev_leds = {}   # dict mapping pixel_index -> (R, G, B)

# Color helpers

def hsl_to_rgb(h, s, l):
    h = h % 360
    s /= 100
    l /= 100
    c = (1 - abs(2 * l - 1)) * s
    x = c * (1 - abs((h / 60) % 2 - 1))
    m = l - c / 2
    if   h < 60:  r, g, b = c, x, 0
    elif h < 120: r, g, b = x, c, 0
    elif h < 180: r, g, b = 0, c, x
    elif h < 240: r, g, b = 0, x, c
    elif h < 300: r, g, b = x, 0, c
    else:         r, g, b = c, 0, x
    return int((r + m) * 255), int((g + m) * 255), int((b + m) * 255)

def rgb_to_hex(r, g, b):
    return '#{:02x}{:02x}{:02x}'.format(r, g, b)

def hex_to_rgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


# Color wheel

def draw_color_wheel():
    wheel_canvas.delete('all')
    size = wheel_radius * 2
    cx = cy = wheel_radius
    photo = tk.PhotoImage(width=size, height=size)
    pixels = []
    for y in range(size):
        row_colors = []
        for x in range(size):
            dx, dy = x - cx, y - cy
            dist = math.sqrt(dx*dx + dy*dy)
            if dist <= wheel_radius:
                angle = (math.degrees(math.atan2(dy, dx)) + 360) % 360
                sat   = (dist / wheel_radius) * 100
                r, g, b = hsl_to_rgb(angle, sat, 100 - brightness * 100)
                row_colors.append('#{:02x}{:02x}{:02x}'.format(r, g, b))
            else:
                row_colors.append(None)
        pixels.append(row_colors)

    for y, row_colors in enumerate(pixels):
        for x, c in enumerate(row_colors):
            if c:
                photo.put(c, (x, y))

    wheel_canvas._photo = photo
    wheel_canvas.create_image(0, 0, anchor='nw', image=photo)

    cx_dot = cx + saturation * wheel_radius * math.cos(math.radians(hue))
    cy_dot = cy + saturation * wheel_radius * math.sin(math.radians(hue))
    r = 6
    wheel_canvas.create_oval(cx_dot-r, cy_dot-r, cx_dot+r, cy_dot+r,
                              outline='white', width=2)
    wheel_canvas.create_oval(cx_dot-r-1, cy_dot-r-1, cx_dot+r+1, cy_dot+r+1,
                              outline='#555', width=1)

def pick_wheel_color(event):
    global hue, saturation, brush_color
    cx = cy = wheel_radius
    dx, dy = event.x - cx, event.y - cy
    dist = math.sqrt(dx*dx + dy*dy)
    if dist <= wheel_radius:
        hue        = (math.degrees(math.atan2(dy, dx)) + 360) % 360
        saturation = min(dist / wheel_radius, 1.0)
        _update_brush_color()
        draw_color_wheel()

def _update_brush_color():
    global brush_color
    r, g, b    = hsl_to_rgb(hue, saturation * 100, 100 - brightness * 100)
    brush_color = rgb_to_hex(r, g, b)
    color_preview.config(bg=brush_color)

def on_brightness_change(val):
    global brightness
    brightness = float(val)
    _update_brush_color()
    draw_color_wheel()


# Canvas / drawing

def redraw_canvas():
    canvas.config(width=GRID_COLS * BLOCK_SIZE, height=GRID_ROWS * BLOCK_SIZE)
    canvas.delete('all')
    for y in range(GRID_ROWS):
        for x in range(GRID_COLS):
            x0, y0 = x * BLOCK_SIZE, y * BLOCK_SIZE
            color = image.getpixel((x0, y0))
            # hex_color = '#ffffff' if color[3] == 0 else \
            #             '#{:02x}{:02x}{:02x}'.format(*color[:3])
            hex_color = '#{:02x}{:02x}{:02x}'.format(*color[:3])
            canvas.create_rectangle(x0, y0, x0+BLOCK_SIZE, y0+BLOCK_SIZE,
                        fill=hex_color, outline='#333333', width=1)

def start_paint(event):
    global is_painting
    is_painting = True
    apply_tool(event)

def stop_paint(event):
    global is_painting
    is_painting = False

def on_motion(event):
    if is_painting and current_tool != 'fill':
        apply_tool(event)

def apply_tool(event):
    xi = event.x // BLOCK_SIZE
    yi = event.y // BLOCK_SIZE
    if not (0 <= xi < GRID_COLS and 0 <= yi < GRID_ROWS):
        return
    if current_tool == 'draw':
        paint_pixel(xi, yi, brush_color)
    elif current_tool == 'erase':
        paint_pixel(xi, yi, '#000000')
    elif current_tool == 'fill':
        flood_fill(xi, yi)

def paint_pixel(xi, yi, color):
    global dirty
    x0, y0 = xi * BLOCK_SIZE, yi * BLOCK_SIZE
    x1, y1 = x0 + BLOCK_SIZE - 1, y0 + BLOCK_SIZE - 1  # -1 to stay within cell
    rgb = hex_to_rgb(color)
    draw_img.rectangle([(x0, y0), (x1, y1)], fill=(*rgb, 255))
    canvas.create_rectangle(x0, y0, x0 + BLOCK_SIZE, y0 + BLOCK_SIZE,
                             fill=color, outline='#333333', width=1)
    dirty = True

def flood_fill(xi, yi):
    target   = image.getpixel((xi * BLOCK_SIZE, yi * BLOCK_SIZE))[:3]
    fill_rgb = hex_to_rgb(brush_color)
    if target == fill_rgb:
        return
    stack, visited = [(xi, yi)], set()
    while stack:
        x, y = stack.pop()
        if (x, y) in visited or not (0 <= x < GRID_COLS and 0 <= y < GRID_ROWS):
            continue
        if image.getpixel((x * BLOCK_SIZE, y * BLOCK_SIZE))[:3] != target:
            continue
        visited.add((x, y))
        stack.extend([(x+1,y),(x-1,y),(x,y+1),(x,y-1)])
    for x, y in visited:
        paint_pixel(x, y, brush_color)

def set_tool(tool_name):
    global current_tool
    current_tool = tool_name
    for name, btn in tool_buttons.items():
        btn.config(relief='sunken' if name == tool_name else 'raised',
                   bg='#dde'     if name == tool_name else '#f0f0f0')

def new_image():
    global image, draw_img, dirty
    prev_leds.clear()
    image = Image.new('RGBA',
                (GRID_COLS * BLOCK_SIZE, GRID_ROWS * BLOCK_SIZE),
                (0, 0, 0, 255))
    draw_img = ImageDraw.Draw(image)
    dirty = True
    redraw_canvas()
    _send_blackout()


# Forces all LEDs to turn off
def _send_blackout():
    if not ser or not ser.is_open:
        return
    PHYSICAL_COLS = NUM_MATRICES * GRID_COLS
    PHYSICAL_ROWS = MATRIX_HEIGHT
    total = PHYSICAL_COLS * PHYSICAL_ROWS

    with ser_lock:
        try:
            for chunk_start in range(0, total, CHUNK_SIZE):
                chunk_end = min(chunk_start + CHUNK_SIZE, total)
                count = chunk_end - chunk_start
                pkt = bytearray([0xFF, 0xFE, (count >> 8) & 0xFF, count & 0xFF])
                for pixel_index in range(chunk_start, chunk_end):
                    pkt.extend([(pixel_index >> 8) & 0xFF, pixel_index & 0xFF, 0, 0, 0])
                ser.write(pkt)
                ack = ser.read(1)
                if ack != b'K':
                    _set_status('error', f'Blackout chunk {chunk_start}–{chunk_end} no ACK')
                    return
            _set_status('ok', 'Cleared')
        except Exception as e:
            _set_status('error', f'Blackout failed: {e}')


def open_image():
    global image, draw_img, dirty
    path = filedialog.askopenfilename(
        filetypes=[('PNG files', '*.png'), ('All files', '*.*')])
    if path:
        img      = Image.open(path).convert('RGBA')
        image    = img.resize((GRID_COLS * BLOCK_SIZE, GRID_ROWS * BLOCK_SIZE),
                               Image.NEAREST)
        draw_img = ImageDraw.Draw(image)
        dirty = True
        redraw_canvas()

def save_image():
    path = filedialog.asksaveasfilename(
        defaultextension='.png',
        filetypes=[('PNG files', '*.png'), ('All files', '*.*')])
    if path:
        image.resize((GRID_COLS, GRID_ROWS), Image.NEAREST).save(path)


# Serial / Arduino

def _build_frame():
    global prev_leds
    changed = []

    PHYSICAL_COLS = NUM_MATRICES * GRID_COLS   # 128 — full strip width
    PHYSICAL_ROWS = MATRIX_HEIGHT              # 8

    for canvas_row in range(GRID_ROWS):        # 0-31 in the UI
        for canvas_col in range(GRID_COLS):    # 0-31 in the UI
            # Convert canvas (col, row) to physical strip coordinates
            matrix_index  = canvas_row // PHYSICAL_ROWS   # which matrix (0-3)
            local_row     = canvas_row %  PHYSICAL_ROWS   # row within that matrix (0-7)
            physical_col  = matrix_index * GRID_COLS + canvas_col  # col across full strip

            pixel_index = local_row * PHYSICAL_COLS + physical_col

            px  = image.getpixel((canvas_col * BLOCK_SIZE, canvas_row * BLOCK_SIZE))
            rgb = (px[0], px[1], px[2])
            if prev_leds.get(pixel_index) != rgb:
                changed.append((pixel_index, rgb))

    if not changed:
        return None

    for pixel_index, rgb in changed:
        prev_leds[pixel_index] = rgb

    count = len(changed)
    pkt = bytearray([0xFF, 0xFE, (count >> 8) & 0xFF, count & 0xFF])
    for pixel_index, (r, g, b) in changed:
        pkt.extend([(pixel_index >> 8) & 0xFF, pixel_index & 0xFF, r, g, b])
    return pkt


# Send a pre-built delta packet in chunks, waiting for ACK each time.
def _send_frame_chunked(frame_pkt):
    total_pixels = (frame_pkt[2] << 8) | frame_pkt[3]
    pixel_data = frame_pkt[4:]
    

    with ser_lock:
        for i in range(0, total_pixels, CHUNK_SIZE):
            chunk_pixels = pixel_data[i*5 : (i+CHUNK_SIZE)*5]
            count = len(chunk_pixels) // 5
            pkt = bytearray([0xFF, 0xFE, (count >> 8) & 0xFF, count & 0xFF])
            pkt.extend(chunk_pixels)
            ser.write(pkt)
            ack = ser.read(1)
            if ack != b'K':
                raise Exception(f'No ACK at chunk offset {i}')

def _serial_sender():
    global dirty, ser
    while True:
        time.sleep(1 / 30)
        if ser and ser.is_open and dirty:
            dirty = False
            frame = _build_frame()
            if frame is None:
                continue
            try:
                _send_frame_chunked(frame)
            except Exception as e:
                print(f'Send error: {e}')
                ser = None
                root.after(0, lambda err=e: _set_status('error', f'Lost connection: {err}'))

def _refresh_ports():
    ports = [p.device for p in serial.tools.list_ports.comports()]
    port_combo['values'] = ports
    if ports and not port_var.get():
        port_combo.current(0)

def _toggle_connect():
    global ser
    if ser and ser.is_open:
        ser.close()
        ser = None
        _set_status('off', 'Disconnected')
        connect_btn.config(text='Connect')
    else:
        port = port_var.get()
        if not port:
            _set_status('error', 'No port selected')
            return
        try:
            ser = serial.Serial(port, BAUD_RATE, timeout=2)
            time.sleep(3)           # wait for Arduino reset
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            _set_status('ok', f'Connected  {port}')
            connect_btn.config(text='Disconnect')
        except Exception as e:
            _set_status('error', str(e))

def _set_status(state, text):
    colors = {'ok': 'green', 'error': 'red', 'off': 'gray'}
    status_lbl.config(text=text, fg=colors.get(state, 'gray'))


# def _send_test():
#     """Send all-red frame directly, bypassing the dirty flag."""
#     if not ser or not ser.is_open:
#         _set_status('error', 'Not connected')
#         return
#     pkt = bytearray([0xFF, 0xFE])
#     for _ in range(GRID_ROWS * GRID_COLS):
#         pkt.extend((255, 0, 0))  # pure red on every LED
#     try:
#         ser.write(pkt)
#         response = ser.read(1)
#         if response == b'K':
#             _set_status('ok', 'Test OK — got ACK')
#         else:
#             _set_status('error', f'Test sent, no ACK (got {response!r})')
#     except Exception as e:
#         _set_status('error', f'Test failed: {e}')


# UI setup

def setup_app():
    global root, image, draw_img, canvas, wheel_canvas, color_preview
    global brightness_slider, tool_buttons
    global port_var, port_combo, connect_btn, status_lbl

    root = tk.Tk()
    root.title(f'LED Matrix Painter  —  {GRID_COLS}×{GRID_ROWS}  ({NUM_MATRICES} matrix)')
    root.resizable(False, False)

    image    = Image.new('RGBA',
                         (GRID_COLS * BLOCK_SIZE, GRID_ROWS * BLOCK_SIZE),
                         (0, 0, 0, 255))
    draw_img = ImageDraw.Draw(image)

    # Menu
    menu_bar  = tk.Menu(root)
    file_menu = tk.Menu(menu_bar, tearoff=0)
    menu_bar.add_cascade(label='File', menu=file_menu)
    file_menu.add_command(label='New',  command=new_image)
    file_menu.add_command(label='Open', command=open_image)
    file_menu.add_command(label='Save', command=save_image)
    file_menu.add_separator()
    file_menu.add_command(label='Exit', command=root.destroy)
    root.config(menu=menu_bar)

    main_frame = tk.Frame(root)
    main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

    # Sidebar
    sidebar = tk.Frame(main_frame, width=210)
    sidebar.grid(row=0, column=0, sticky='ns', padx=(0, 12))

    # Serial connection
    tk.Label(sidebar, text='Arduino', font=('TkDefaultFont', 10, 'bold')).pack(anchor='w')
    port_frame = tk.Frame(sidebar)
    port_frame.pack(fill='x', pady=(2, 0))

    port_var   = tk.StringVar()
    port_combo = tk.ttk.Combobox(port_frame, textvariable=port_var, width=12)
    port_combo.pack(side='left')

    tk.Button(port_frame, text='↻', width=2,
              command=_refresh_ports).pack(side='left', padx=2)

    connect_btn = tk.Button(sidebar, text='Connect', command=_toggle_connect)
    connect_btn.pack(fill='x', pady=(4, 0))

    status_lbl = tk.Label(sidebar, text='●  not connected', fg='gray',
                           anchor='w', font=('TkDefaultFont', 9))
    status_lbl.pack(fill='x', pady=(2, 8))

    # Color wheel
    tk.Label(sidebar, text='Color', font=('TkDefaultFont', 10, 'bold')).pack(anchor='w')
    wheel_size   = wheel_radius * 2
    wheel_canvas = tk.Canvas(sidebar, width=wheel_size, height=wheel_size,
                              bg='white', highlightthickness=1,
                              highlightbackground='#cccccc', cursor='crosshair')
    wheel_canvas.pack(pady=(2, 0))
    wheel_canvas.bind('<Button-1>',   pick_wheel_color)
    wheel_canvas.bind('<B1-Motion>',  pick_wheel_color)

    tk.Label(sidebar, text='Brightness', font=('TkDefaultFont', 9)).pack(anchor='w', pady=(6,0))
    brightness_slider = tk.Scale(sidebar, from_=0.0, to=1.0, resolution=0.01,
                                  orient='horizontal', command=on_brightness_change,
                                  length=160, showvalue=False)
    brightness_slider.set(0.5)
    brightness_slider.pack(fill='x')

    tk.Label(sidebar, text='Current color', font=('TkDefaultFont', 9)).pack(anchor='w', pady=(6,2))
    color_preview = tk.Label(sidebar, bg=brush_color, width=18, height=2,
                              relief='solid', bd=1)
    color_preview.pack(fill='x')

    # Tools
    tk.Label(sidebar, text='Tool', font=('TkDefaultFont', 10, 'bold')).pack(anchor='w', pady=(10,4))
    tool_frame  = tk.Frame(sidebar)
    tool_frame.pack(fill='x')
    tool_buttons = {}
    for name in ['draw', 'erase', 'fill']:
        btn = tk.Button(tool_frame, text=name.capitalize(), width=5,
                        command=lambda t=name: set_tool(t))
        btn.pack(side='left', padx=2)
        tool_buttons[name] = btn
    set_tool('draw')

    tk.Button(tool_frame, text='Clear', width=5,
          command=new_image).pack(side='left', padx=2)
    
    # tk.Button(tool_frame, text='Test', width=5,
    #       command=_send_test).pack(side='left', padx=2)

    # Matrix config info
    tk.Label(sidebar, text='Matrix config',
             font=('TkDefaultFont', 10, 'bold')).pack(anchor='w', pady=(14, 2))
    tk.Label(sidebar,
             text=f'Matrices: {NUM_MATRICES}\nGrid: {GRID_COLS}×{GRID_ROWS}\nBaud: {BAUD_RATE}',
             font=('TkDefaultFont', 9), justify='left',
             fg='gray').pack(anchor='w')

    # Drawing canvas
    canvas_frame = tk.Frame(main_frame, bg='#e8e8e8')
    canvas_frame.grid(row=0, column=1, sticky='nsew')

    global canvas
    canvas = tk.Canvas(canvas_frame, bg='black',
                       highlightthickness=1, highlightbackground='#cccccc',
                       cursor='crosshair')
    canvas.pack()
    canvas.bind('<Button-1>',        start_paint)
    canvas.bind('<B1-Motion>',       on_motion)
    canvas.bind('<ButtonRelease-1>', stop_paint)

    _refresh_ports()
    redraw_canvas()
    draw_color_wheel()

    threading.Thread(target=_serial_sender, daemon=True).start()


if __name__ == '__main__':
    from tkinter import ttk
    setup_app()
    root.mainloop()