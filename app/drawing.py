"""Drawy desktop painting app (UI + entry point).

All ESP32 communication lives in link.py; constants live in config.py;
color math lives in colorutils.py. Run with:  python drawing.py
"""

import math
import threading
import tkinter as tk
from tkinter import filedialog, ttk

from PIL import Image, ImageDraw, ImageTk, ImageSequence

from config import (
    ESP32_IP, ESP32_PORT, NUM_MATRICES, MATRIX_HEIGHT,
    GRID_COLS, GRID_ROWS,
)
from colorutils import hsl_to_rgb, rgb_to_hsl, rgb_to_hex, hex_to_rgb
from link import LedLink, list_serial_ports

# The canvas zoom (pixels per grid cell) is DYNAMIC: it is recomputed
# whenever the window is resized, so the whole app scales like any normal
# window. Frames are stored at native grid resolution (32x32), independent
# of how big they are drawn on screen.
MIN_CELL  = 6        # don't let cells get smaller than this
cell_size = 20       # current zoom; updated on window resize

root         = None
brush_color  = '#000000'
is_painting  = False
image        = None
draw_img     = None
current_tool = 'draw'

# 'shade' value is the COLOR lightness (color wheel), NOT the LED
# panel brightness. Panel brightness is a separate control sent to the ESP32.
hue        = 0.0
saturation = 1.0
shade      = 0.5
wheel_radius = 80

link = None          # LedLink instance, created in setup_app()

# Animation state
frames        = []      # list of native-resolution PIL Images
current_frame = 0
is_playing    = False
play_job      = None    # root.after() id while playing

THUMB_W = 48
THUMB_H = max(1, int(THUMB_W * GRID_ROWS / GRID_COLS))

thumb_refs    = []      # keep PhotoImage references alive
thumb_buttons = []
canvas_rects  = []      # one canvas rectangle per grid cell, created once
_resize_job   = None    # debounce id for window-resize events


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
                r, g, b = hsl_to_rgb(angle, sat, 100 - shade * 100)
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
    r, g, b     = hsl_to_rgb(hue, saturation * 100, 100 - shade * 100)
    brush_color = rgb_to_hex(r, g, b)
    color_preview.config(bg=brush_color)


def on_shade_change(val):
    global shade
    shade = float(val)
    _update_brush_color()
    draw_color_wheel()


# Canvas / drawing
def _init_canvas_cells():
    # Create every grid cell ONCE; redraws just recolor them and window
    # resizes just move/scale them. This keeps everything fast.
    canvas.config(width=GRID_COLS * cell_size, height=GRID_ROWS * cell_size)
    canvas.delete('all')
    canvas_rects.clear()
    for y in range(GRID_ROWS):
        for x in range(GRID_COLS):
            x0, y0 = x * cell_size, y * cell_size
            rid = canvas.create_rectangle(x0, y0, x0+cell_size, y0+cell_size,
                                          fill='#000000', outline='#333333',
                                          width=1)
            canvas_rects.append(rid)


def redraw_canvas():
    for y in range(GRID_ROWS):
        for x in range(GRID_COLS):
            color = image.getpixel((x, y))
            canvas.itemconfig(canvas_rects[y * GRID_COLS + x],
                              fill='#{:02x}{:02x}{:02x}'.format(*color[:3]))


# Window resizing: pick the biggest cell size that fits, then move the
# existing rectangles. Debounced so dragging the window edge stays smooth.
def _on_canvas_area_resize(event=None):
    global _resize_job
    if _resize_job is not None:
        root.after_cancel(_resize_job)
    _resize_job = root.after(60, _apply_canvas_size)


def _apply_canvas_size():
    global cell_size, _resize_job
    _resize_job = None
    w = canvas_frame.winfo_width()  - 4
    h = canvas_frame.winfo_height() - 4
    if w <= 0 or h <= 0:
        return
    cell = max(MIN_CELL, min(w // GRID_COLS, h // GRID_ROWS))
    if cell == cell_size:
        return
    cell_size = cell
    canvas.config(width=GRID_COLS * cell, height=GRID_ROWS * cell)
    for y in range(GRID_ROWS):
        for x in range(GRID_COLS):
            rid = canvas_rects[y * GRID_COLS + x]
            canvas.coords(rid, x * cell, y * cell,
                          (x + 1) * cell, (y + 1) * cell)


def start_paint(event):
    global is_painting
    if is_playing:
        _stop_playback()
    is_painting = True
    apply_tool(event)


def stop_paint(event):
    global is_painting
    if is_painting and current_tool in ('draw', 'erase'):
        _refresh_current_thumb()
    is_painting = False


def on_motion(event):
    if is_painting and current_tool in ('draw', 'erase'):
        apply_tool(event)


def apply_tool(event):
    xi = event.x // cell_size
    yi = event.y // cell_size
    if not (0 <= xi < GRID_COLS and 0 <= yi < GRID_ROWS):
        return
    if   current_tool == 'draw':  paint_pixel(xi, yi, brush_color)
    elif current_tool == 'erase': paint_pixel(xi, yi, '#000000')
    elif current_tool == 'fill':  flood_fill(xi, yi)
    elif current_tool == 'pick':  pick_canvas_color(xi, yi)


def paint_pixel(xi, yi, color):
    rgb = hex_to_rgb(color)
    image.putpixel((xi, yi), (*rgb, 255))
    canvas.itemconfig(canvas_rects[yi * GRID_COLS + xi], fill=color)
    link.mark_dirty()


# Eyedropper: grab the color under the cursor, sync the wheel + shade
# slider to it, then hop back to the Draw tool.
def pick_canvas_color(xi, yi):
    global hue, saturation, shade, is_painting
    rgb = image.getpixel((xi, yi))[:3]
    h, s, l = rgb_to_hsl(*rgb)
    hue        = h
    saturation = s / 100
    shade      = 1 - l / 100
    shade_slider.set(shade)        # keep the slider in sync
    _update_brush_color()
    draw_color_wheel()
    is_painting = False            # don't start drawing on the same drag
    set_tool('draw')


def flood_fill(xi, yi):
    target   = image.getpixel((xi, yi))[:3]
    fill_rgb = hex_to_rgb(brush_color)
    if target == fill_rgb:
        return
    stack, visited = [(xi, yi)], set()
    while stack:
        x, y = stack.pop()
        if (x, y) in visited or not (0 <= x < GRID_COLS and 0 <= y < GRID_ROWS):
            continue
        if image.getpixel((x, y))[:3] != target:
            continue
        visited.add((x, y))
        stack.extend([(x+1,y),(x-1,y),(x,y+1),(x,y-1)])
    for x, y in visited:
        image.putpixel((x, y), (*fill_rgb, 255))
    link.mark_dirty()
    redraw_canvas()
    _refresh_current_thumb()


def set_tool(tool_name):
    global current_tool
    current_tool = tool_name
    for name, btn in tool_buttons.items():
        btn.config(relief='sunken' if name == tool_name else 'raised',
                   bg='#dde'     if name == tool_name else '#f0f0f0')


# Animation: frames (stored at native grid resolution)
def _new_blank_frame():
    return Image.new('RGBA', (GRID_COLS, GRID_ROWS), (0, 0, 0, 255))


def _select_frame(idx, rebuild=False):
    """Make frames[idx] the one shown on the canvas (and on the LEDs)."""
    global current_frame, image, draw_img
    current_frame = idx
    image    = frames[idx]
    draw_img = ImageDraw.Draw(image)
    redraw_canvas()
    if rebuild:
        _rebuild_frame_strip()
    else:
        _update_strip_selection()
    link.mark_dirty()    # the LED panel always shows the selected frame


def add_frame():
    _stop_playback()
    frames.insert(current_frame + 1, _new_blank_frame())
    _select_frame(current_frame + 1, rebuild=True)


def duplicate_frame():
    _stop_playback()
    frames.insert(current_frame + 1, frames[current_frame].copy())
    _select_frame(current_frame + 1, rebuild=True)


def delete_frame():
    _stop_playback()
    if len(frames) == 1:
        clear_frame()            # deleting the last frame just clears it
        return
    frames.pop(current_frame)
    _select_frame(min(current_frame, len(frames) - 1), rebuild=True)


def clear_frame():
    _stop_playback()
    draw_img.rectangle([(0, 0), (GRID_COLS - 1, GRID_ROWS - 1)],
                       fill=(0, 0, 0, 255))
    link.mark_dirty()
    redraw_canvas()
    _refresh_current_thumb()


def _step_frame(delta):
    w = root.focus_get()
    if isinstance(w, (tk.Entry, tk.Spinbox, ttk.Combobox)):
        return                   # don't steal arrow keys from text fields
    _stop_playback()
    _select_frame((current_frame + delta) % len(frames))


# Animation: frame strip UI
def _rebuild_frame_strip():
    for w in strip_inner.winfo_children():
        w.destroy()
    thumb_refs.clear()
    thumb_buttons.clear()
    for i, f in enumerate(frames):
        ph = ImageTk.PhotoImage(f.resize((THUMB_W, THUMB_H), Image.NEAREST))
        thumb_refs.append(ph)
        btn = tk.Button(strip_inner, image=ph, text=str(i + 1),
                        compound='top', font=('TkDefaultFont', 8), bd=1,
                        command=lambda i=i: (_stop_playback(),
                                             _select_frame(i)))
        btn.pack(side='left', padx=2, pady=2)
        thumb_buttons.append(btn)
    _update_strip_selection()
    strip_inner.update_idletasks()
    strip_canvas.config(scrollregion=strip_canvas.bbox('all') or (0, 0, 0, 0))


def _update_strip_selection():
    for i, btn in enumerate(thumb_buttons):
        sel = (i == current_frame)
        btn.config(bg='#cde' if sel else '#f0f0f0',
                   relief='sunken' if sel else 'raised')
    frame_pos_lbl.config(text=f'Frame {current_frame + 1} / {len(frames)}')


def _refresh_current_thumb():
    if current_frame < len(thumb_buttons):
        ph = ImageTk.PhotoImage(
            frames[current_frame].resize((THUMB_W, THUMB_H), Image.NEAREST))
        thumb_refs[current_frame] = ph
        thumb_buttons[current_frame].config(image=ph)


# Animation: playback (the LED wall plays along, since selecting a frame
# marks it dirty and the sender thread pushes the delta)
def _toggle_play():
    global is_playing
    if is_playing:
        _stop_playback()
        return
    if len(frames) < 2:
        return
    is_playing = True
    play_btn.config(text='\u25a0 Stop')
    _play_step()


def _play_step():
    global play_job
    if not is_playing:
        return
    _select_frame((current_frame + 1) % len(frames))
    delay = max(20, int(1000 / max(1, _get_fps())))
    play_job = root.after(delay, _play_step)


def _stop_playback():
    global is_playing, play_job
    is_playing = False
    if play_job is not None:
        root.after_cancel(play_job)
        play_job = None
    if play_btn is not None:
        play_btn.config(text='\u25b6 Play')


def _get_fps():
    try:
        return max(1, min(30, int(fps_var.get())))
    except Exception:
        return 8


# File menu
def new_animation():
    global frames
    _stop_playback()
    frames[:] = [_new_blank_frame()]
    link.reset_history()
    _select_frame(0, rebuild=True)


def open_image():
    global frames
    path = filedialog.askopenfilename(
        filetypes=[('Images', '*.png *.gif'),
                   ('PNG files', '*.png'),
                   ('GIF files', '*.gif'),
                   ('All files', '*.*')])
    if not path:
        return
    _stop_playback()
    img  = Image.open(path)
    size = (GRID_COLS, GRID_ROWS)
    if getattr(img, 'n_frames', 1) > 1:
        # Animated GIF -> replaces the whole animation
        frames[:] = [f.convert('RGBA').resize(size, Image.NEAREST)
                     for f in ImageSequence.Iterator(img)]
        link.reset_history()
        _select_frame(0, rebuild=True)
    else:
        # Still image -> loads into the CURRENT frame only
        frames[current_frame] = img.convert('RGBA').resize(size, Image.NEAREST)
        _select_frame(current_frame, rebuild=True)


def save_image():
    path = filedialog.asksaveasfilename(
        defaultextension='.png',
        filetypes=[('PNG files', '*.png'), ('All files', '*.*')])
    if path:
        image.save(path)


def export_gif():
    path = filedialog.asksaveasfilename(
        defaultextension='.gif',
        filetypes=[('GIF files', '*.gif'), ('All files', '*.*')])
    if not path:
        return
    scale = 8   # upscale so the GIF is viewable; Open re-downscales fine
    out = [f.resize((GRID_COLS * scale, GRID_ROWS * scale), Image.NEAREST)
            .convert('RGB')
           for f in frames]
    out[0].save(path, save_all=True, append_images=out[1:],
                duration=int(1000 / _get_fps()), loop=0)


# LED panel brightness (separate from color shade) -> sent to the ESP32
def on_led_brightness_release(event=None):
    link.set_brightness(int(float(led_brightness_slider.get())))


# Connection (transport selector: Wi-Fi or cable)
def _refresh_ports():
    ports = list_serial_ports()
    port_combo['values'] = ports
    if ports and not port_var.get():
        port_combo.current(0)


def _toggle_connect():
    if link.is_connected():
        link.disconnect()
        return
    if transport_var.get() == 'wifi':
        threading.Thread(
            target=lambda: link.connect_wifi(ip_var.get(),
                                             port_num_var.get() or ESP32_PORT),
            daemon=True).start()
    else:
        link.connect_cable(port_var.get())


# LedLink callbacks: may fire from a worker thread, so marshal them onto
# the Tk main thread with root.after.
def _on_link_status(state, text):
    root.after(0, lambda: _set_status(state, text))


def _on_link_conn_change(connected):
    root.after(0, lambda: connect_btn.config(
        text='Disconnect' if connected else 'Connect'))


def _set_status(state, text):
    colors = {'ok': 'green', 'error': 'red', 'off': 'gray'}
    status_lbl.config(text=text, fg=colors.get(state, 'gray'))


def _on_transport_change():
    if transport_var.get() == 'wifi':
        cable_frame.pack_forget()
        wifi_frame.pack(fill='x', pady=(2, 0), after=transport_frame)
    else:
        wifi_frame.pack_forget()
        cable_frame.pack(fill='x', pady=(2, 0), after=transport_frame)


# UI
def setup_app():
    global root, image, draw_img, canvas, canvas_frame, wheel_canvas
    global color_preview, shade_slider, led_brightness_slider, tool_buttons
    global port_var, port_combo, connect_btn, status_lbl
    global transport_var, transport_frame, wifi_frame, cable_frame
    global ip_var, port_num_var
    global play_btn, fps_var, frame_pos_lbl, strip_canvas, strip_inner
    global link

    root = tk.Tk()
    root.title(f'Drawy  \u2014  {GRID_COLS}x{GRID_ROWS}  ({NUM_MATRICES} panels)')

    link = LedLink(get_image=lambda: image,
                   on_status=_on_link_status,
                   on_conn_change=_on_link_conn_change)

    # Resizable window: start at a size that fits most screens, never
    # smaller than what the sidebar + a minimum canvas need.
    root.resizable(True, True)
    start_w = min(root.winfo_screenwidth()  - 80, 980)
    start_h = min(root.winfo_screenheight() - 120, 760)
    root.geometry(f'{start_w}x{start_h}')
    root.minsize(540, 360)   # sidebar scrolls, so short windows are fine

    frames.append(_new_blank_frame())
    image    = frames[0]
    draw_img = ImageDraw.Draw(image)

    menu_bar  = tk.Menu(root)
    file_menu = tk.Menu(menu_bar, tearoff=0)
    menu_bar.add_cascade(label='File', menu=file_menu)
    file_menu.add_command(label='New',            command=new_animation)
    file_menu.add_command(label='Open (PNG/GIF)', command=open_image)
    file_menu.add_command(label='Save frame (PNG)', command=save_image)
    file_menu.add_command(label='Export GIF',     command=export_gif)
    file_menu.add_separator()
    file_menu.add_command(label='Exit', command=root.destroy)
    root.config(menu=menu_bar)

    main_frame = tk.Frame(root)
    main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
    main_frame.columnconfigure(1, weight=1)   # canvas column absorbs resize
    main_frame.rowconfigure(0, weight=1)      # canvas row absorbs resize

    # Sidebar — scrollable, so the tools never get cut off in short windows.
    # The scrollbar only appears when the content doesn't fit.
    sidebar_outer = tk.Frame(main_frame)
    sidebar_outer.grid(row=0, column=0, rowspan=2, sticky='ns', padx=(0, 12))

    sidebar_canvas = tk.Canvas(sidebar_outer, width=200,
                               highlightthickness=0, yscrollincrement=20)
    sidebar_scroll = tk.Scrollbar(sidebar_outer, orient='vertical',
                                  command=sidebar_canvas.yview)
    sidebar_canvas.config(yscrollcommand=sidebar_scroll.set)
    sidebar_canvas.pack(side='left', fill='both', expand=True)

    sidebar = tk.Frame(sidebar_canvas)
    sidebar_canvas.create_window((0, 0), window=sidebar, anchor='nw')

    def _sync_sidebar(event=None):
        sidebar_canvas.config(
            scrollregion=sidebar_canvas.bbox('all') or (0, 0, 0, 0),
            width=sidebar.winfo_reqwidth())
        if sidebar.winfo_reqheight() > sidebar_canvas.winfo_height():
            sidebar_scroll.pack(side='right', fill='y')
        else:
            sidebar_scroll.pack_forget()
            sidebar_canvas.yview_moveto(0)   # snap back when it fits again

    sidebar.bind('<Configure>', _sync_sidebar)
    sidebar_canvas.bind('<Configure>', _sync_sidebar)

    def _on_global_wheel(event):
        # Scroll the sidebar only when the pointer is over it AND it
        # actually overflows. (str(widget) is the Tk path, so startswith
        # checks "is inside the sidebar".)
        w = root.winfo_containing(event.x_root, event.y_root)
        if w is None or not str(w).startswith(str(sidebar_outer)):
            return
        if sidebar.winfo_reqheight() <= sidebar_canvas.winfo_height():
            return
        if getattr(event, 'num', None) == 4 or event.delta > 0:
            sidebar_canvas.yview_scroll(-2, 'units')
        else:
            sidebar_canvas.yview_scroll(2, 'units')

    # Windows/macOS deliver <MouseWheel>; Linux delivers Button-4/5.
    root.bind_all('<MouseWheel>', _on_global_wheel)
    root.bind_all('<Button-4>',  _on_global_wheel)
    root.bind_all('<Button-5>',  _on_global_wheel)

    # Connection
    tk.Label(sidebar, text='Connection',
             font=('TkDefaultFont', 10, 'bold')).pack(anchor='w')

    transport_var = tk.StringVar(value='wifi')
    transport_frame = tk.Frame(sidebar)
    transport_frame.pack(fill='x', pady=(2, 0))
    tk.Radiobutton(transport_frame, text='Wi-Fi', variable=transport_var,
                   value='wifi', command=_on_transport_change).pack(side='left')
    tk.Radiobutton(transport_frame, text='Cable', variable=transport_var,
                   value='cable', command=_on_transport_change).pack(side='left')

    # Wi-Fi controls
    wifi_frame = tk.Frame(sidebar)
    ip_var       = tk.StringVar(value=ESP32_IP)
    port_num_var = tk.StringVar(value=str(ESP32_PORT))
    tk.Label(wifi_frame, text='IP', font=('TkDefaultFont', 9)).pack(anchor='w')
    tk.Entry(wifi_frame, textvariable=ip_var, width=18).pack(fill='x')
    tk.Label(wifi_frame, text='Port', font=('TkDefaultFont', 9)).pack(anchor='w')
    tk.Entry(wifi_frame, textvariable=port_num_var, width=18).pack(fill='x')

    # Cable controls
    cable_frame = tk.Frame(sidebar)
    port_var   = tk.StringVar()
    port_combo = ttk.Combobox(cable_frame, textvariable=port_var, width=12)
    port_combo.pack(side='left')
    tk.Button(cable_frame, text='\u21bb', width=2,
              command=_refresh_ports).pack(side='left', padx=2)

    _on_transport_change()   # show the right sub-frame

    connect_btn = tk.Button(sidebar, text='Connect', command=_toggle_connect)
    connect_btn.pack(fill='x', pady=(6, 0))

    status_lbl = tk.Label(sidebar, text='\u25cf  not connected', fg='gray',
                          anchor='w', font=('TkDefaultFont', 9))
    status_lbl.pack(fill='x', pady=(2, 8))

    # LED panel brightness
    tk.Label(sidebar, text='LED brightness',
             font=('TkDefaultFont', 10, 'bold')).pack(anchor='w')
    led_brightness_slider = tk.Scale(sidebar, from_=0, to=255, resolution=1,
                                     orient='horizontal', length=180,
                                     showvalue=True)
    led_brightness_slider.set(50)
    led_brightness_slider.pack(fill='x')
    # Send only on release so we don't flood the link while dragging.
    led_brightness_slider.bind('<ButtonRelease-1>', on_led_brightness_release)

    # Color
    tk.Label(sidebar, text='Color', font=('TkDefaultFont', 10, 'bold')).pack(anchor='w', pady=(8,0))
    wheel_size   = wheel_radius * 2
    global wheel_canvas
    wheel_canvas = tk.Canvas(sidebar, width=wheel_size, height=wheel_size,
                             bg='white', highlightthickness=1,
                             highlightbackground='#cccccc', cursor='crosshair')
    wheel_canvas.pack(pady=(2, 0))
    wheel_canvas.bind('<Button-1>',  pick_wheel_color)
    wheel_canvas.bind('<B1-Motion>', pick_wheel_color)

    tk.Label(sidebar, text='Color shade', font=('TkDefaultFont', 9)).pack(anchor='w', pady=(6,0))
    shade_slider = tk.Scale(sidebar, from_=0.0, to=1.0, resolution=0.01,
                            orient='horizontal', command=on_shade_change,
                            length=160, showvalue=False)
    shade_slider.set(0.5)
    shade_slider.pack(fill='x')

    tk.Label(sidebar, text='Current color', font=('TkDefaultFont', 9)).pack(anchor='w', pady=(6,2))
    color_preview = tk.Label(sidebar, bg=brush_color, width=18, height=2,
                             relief='solid', bd=1)
    color_preview.pack(fill='x')

    # Tools (two rows so the sidebar stays narrow)
    tk.Label(sidebar, text='Tool', font=('TkDefaultFont', 10, 'bold')).pack(anchor='w', pady=(10,4))
    tool_frame = tk.Frame(sidebar)
    tool_frame.pack(fill='x')
    global tool_buttons
    tool_buttons = {}
    layout = {'draw': (0, 0), 'erase': (0, 1), 'fill': (0, 2),
              'pick': (1, 0)}
    for name, (r, c) in layout.items():
        btn = tk.Button(tool_frame, text=name.capitalize(), width=5,
                        command=lambda t=name: set_tool(t))
        btn.grid(row=r, column=c, padx=2, pady=2)
        tool_buttons[name] = btn
    set_tool('draw')
    tk.Button(tool_frame, text='Clear', width=5,
              command=clear_frame).grid(row=1, column=1, padx=2, pady=2)

    # Canvas area: fills all remaining space; the grid scales to fit it.
    canvas_frame = tk.Frame(main_frame, bg='#e8e8e8')
    canvas_frame.grid(row=0, column=1, sticky='nsew')
    canvas_frame.grid_propagate(False)        # size comes from the window,
    canvas_frame.config(width=300, height=300)  # not from the canvas inside
    canvas_frame.bind('<Configure>', _on_canvas_area_resize)

    global canvas
    canvas = tk.Canvas(canvas_frame, bg='black',
                       highlightthickness=1, highlightbackground='#cccccc',
                       cursor='crosshair')
    canvas.place(relx=0.5, rely=0.5, anchor='center')   # stay centered
    canvas.bind('<Button-1>',        start_paint)
    canvas.bind('<B1-Motion>',       on_motion)
    canvas.bind('<ButtonRelease-1>', stop_paint)

    # Animation bar (controls + frame strip) under the canvas
    anim_frame = tk.Frame(main_frame)
    anim_frame.grid(row=1, column=1, sticky='ew', pady=(8, 0))

    ctrl = tk.Frame(anim_frame)
    ctrl.pack(fill='x')

    play_btn = tk.Button(ctrl, text='\u25b6 Play', width=7,
                         command=_toggle_play)
    play_btn.pack(side='left')

    tk.Label(ctrl, text='FPS').pack(side='left', padx=(10, 2))
    fps_var = tk.IntVar(value=8)
    tk.Spinbox(ctrl, from_=1, to=30, textvariable=fps_var,
               width=4).pack(side='left')

    tk.Button(ctrl, text='+ Add', width=7,
              command=add_frame).pack(side='left', padx=(18, 2))
    tk.Button(ctrl, text='Duplicate', width=8,
              command=duplicate_frame).pack(side='left', padx=2)
    tk.Button(ctrl, text='Delete', width=6,
              command=delete_frame).pack(side='left', padx=2)

    frame_pos_lbl = tk.Label(ctrl, text='Frame 1 / 1', fg='#555',
                             font=('TkDefaultFont', 9))
    frame_pos_lbl.pack(side='right')

    # Scrollable thumbnail strip
    strip_canvas = tk.Canvas(anim_frame, height=THUMB_H + 28,
                             highlightthickness=0)
    strip_scroll = tk.Scrollbar(anim_frame, orient='horizontal',
                                command=strip_canvas.xview)
    strip_canvas.config(xscrollcommand=strip_scroll.set)
    strip_inner = tk.Frame(strip_canvas)
    strip_canvas.create_window((0, 0), window=strip_inner, anchor='nw')
    strip_canvas.pack(fill='x', pady=(4, 0))
    strip_scroll.pack(fill='x')

    # Left/Right arrows step through frames (when not typing in a field)
    root.bind('<Left>',  lambda e: _step_frame(-1))
    root.bind('<Right>', lambda e: _step_frame(1))

    _refresh_ports()
    _init_canvas_cells()
    redraw_canvas()
    draw_color_wheel()
    _rebuild_frame_strip()
    root.after(50, _apply_canvas_size)   # fit the grid to the initial window

    link.start_sender()


if __name__ == '__main__':
    setup_app()
    root.mainloop()
