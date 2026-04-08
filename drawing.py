import tkinter as tk
from tkinter import filedialog
from PIL import Image, ImageDraw
import math

GRID_SIZE = 32
BLOCK_SIZE = 20

root = None
brush_color = '#000000'
is_painting = False
image = None
draw = None
current_tool = 'draw'

hue = 0.0
saturation = 0.0
brightness = 1.0
wheel_radius = 80


def hsl_to_rgb(h, s, l):
    h = h % 360
    s /= 100
    l /= 100
    c = (1 - abs(2 * l - 1)) * s
    x = c * (1 - abs((h / 60) % 2 - 1))
    m = l - c / 2
    if h < 60:   r, g, b = c, x, 0
    elif h < 120: r, g, b = x, c, 0
    elif h < 180: r, g, b = 0, c, x
    elif h < 240: r, g, b = 0, x, c
    elif h < 300: r, g, b = x, 0, c
    else:         r, g, b = c, 0, x
    return int((r + m) * 255), int((g + m) * 255), int((b + m) * 255)


def rgb_to_hex(r, g, b):
    return '#{:02x}{:02x}{:02x}'.format(r, g, b)


def hex_to_rgb(hex_color):
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def draw_color_wheel():
    wheel_canvas.delete('all')
    size = wheel_radius * 2
    cx = cy = wheel_radius
    img_data = []

    for y in range(size):
        row = []
        for x in range(size):
            dx = x - cx
            dy = y - cy
            dist = math.sqrt(dx * dx + dy * dy)
            if dist <= wheel_radius:
                angle = (math.degrees(math.atan2(dy, dx)) + 360) % 360
                sat = (dist / wheel_radius) * 100
                lightness = 50 * brightness
                r, g, b = hsl_to_rgb(angle, sat, lightness)
                row.append('#{:02x}{:02x}{:02x}'.format(r, g, b))
            else:
                row.append(None)
        img_data.append(row)

    wheel_photo = tk.PhotoImage(width=size, height=size)
    for y in range(size):
        for x in range(size):
            color = img_data[y][x]
            if color:
                wheel_photo.put(color, (x, y))

    wheel_canvas._photo = wheel_photo
    wheel_canvas.create_image(0, 0, anchor='nw', image=wheel_photo)

    cx_dot = cx + saturation * wheel_radius * math.cos(math.radians(hue))
    cy_dot = cy + saturation * wheel_radius * math.sin(math.radians(hue))
    r = 6
    wheel_canvas.create_oval(cx_dot - r, cy_dot - r, cx_dot + r, cy_dot + r,
                              outline='white', width=2)
    wheel_canvas.create_oval(cx_dot - r - 1, cy_dot - r - 1, cx_dot + r + 1, cy_dot + r + 1,
                              outline='#555555', width=1)


def pick_wheel_color(event):
    global hue, saturation, brush_color
    cx = cy = wheel_radius
    dx = event.x - cx
    dy = event.y - cy
    dist = math.sqrt(dx * dx + dy * dy)
    if dist <= wheel_radius:
        hue = (math.degrees(math.atan2(dy, dx)) + 360) % 360
        saturation = min(dist / wheel_radius, 1.0)
        update_brush_color()
        draw_color_wheel()


def update_brush_color():
    global brush_color
    lightness = 50 * brightness
    r, g, b = hsl_to_rgb(hue, saturation * 100, lightness)
    brush_color = rgb_to_hex(r, g, b)
    color_preview.config(bg=brush_color)


def on_brightness_change(val):
    global brightness
    brightness = float(val)
    update_brush_color()
    draw_color_wheel()


def redraw_canvas():
    canvas.config(width=GRID_SIZE * BLOCK_SIZE, height=GRID_SIZE * BLOCK_SIZE)
    canvas.delete('all')
    for y in range(GRID_SIZE):
        for x in range(GRID_SIZE):
            x0 = x * BLOCK_SIZE
            y0 = y * BLOCK_SIZE
            x1 = x0 + BLOCK_SIZE
            y1 = y0 + BLOCK_SIZE
            color = image.getpixel((x0, y0))
            if color[3] == 0:
                hex_color = '#ffffff'
            else:
                hex_color = '#{:02x}{:02x}{:02x}'.format(*color[:3])
            canvas.create_rectangle(x0, y0, x1, y1, fill=hex_color,
                                    outline='#cccccc', width=1)


def start_paint(event):
    global is_painting
    is_painting = True
    apply_tool(event)


def stop_paint(event):
    global is_painting
    is_painting = False


def on_motion(event):
    if is_painting:
        apply_tool(event)


def apply_tool(event):
    x_index = event.x // BLOCK_SIZE
    y_index = event.y // BLOCK_SIZE
    if not (0 <= x_index < GRID_SIZE and 0 <= y_index < GRID_SIZE):
        return
    if current_tool == 'draw':
        paint_pixel(x_index, y_index, brush_color)
    elif current_tool == 'erase':
        paint_pixel(x_index, y_index, '#ffffff', erase=True)
    elif current_tool == 'fill' and not is_painting:
        flood_fill(x_index, y_index)


def paint_pixel(xi, yi, color, erase=False):
    x0 = xi * BLOCK_SIZE
    y0 = yi * BLOCK_SIZE
    x1 = x0 + BLOCK_SIZE
    y1 = y0 + BLOCK_SIZE
    if erase:
        draw.rectangle([(x0, y0), (x1, y1)], fill=(255, 255, 255, 0))
        canvas.create_rectangle(x0, y0, x1, y1, fill='#ffffff',
                                 outline='#cccccc', width=1)
    else:
        rgb = hex_to_rgb(color)
        draw.rectangle([(x0, y0), (x1, y1)], fill=(*rgb, 255))
        canvas.create_rectangle(x0, y0, x1, y1, fill=color,
                                 outline='#cccccc', width=1)


def flood_fill(xi, yi):
    target = image.getpixel((xi * BLOCK_SIZE, yi * BLOCK_SIZE))[:3]
    fill_rgb = hex_to_rgb(brush_color)
    if target == fill_rgb:
        return
    stack = [(xi, yi)]
    visited = set()
    while stack:
        x, y = stack.pop()
        if (x, y) in visited:
            continue
        if not (0 <= x < GRID_SIZE and 0 <= y < GRID_SIZE):
            continue
        px = image.getpixel((x * BLOCK_SIZE, y * BLOCK_SIZE))[:3]
        if px != target:
            continue
        visited.add((x, y))
        paint_pixel(x, y, brush_color)
        stack.extend([(x+1,y),(x-1,y),(x,y+1),(x,y-1)])


def set_tool(tool_name):
    global current_tool
    current_tool = tool_name
    for name, btn in tool_buttons.items():
        if name == tool_name:
            btn.config(relief='sunken', bg='#dde')
        else:
            btn.config(relief='raised', bg='#f0f0f0')


def new_image():
    global image, draw
    image = Image.new('RGBA', (GRID_SIZE * BLOCK_SIZE, GRID_SIZE * BLOCK_SIZE), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)
    redraw_canvas()


def open_image():
    file_path = filedialog.askopenfilename(filetypes=[('PNG files', '*.png'),
                                                       ('All files', '*.*')])
    if file_path:
        img = Image.open(file_path).convert('RGBA')
        img_resized = img.resize((GRID_SIZE * BLOCK_SIZE, GRID_SIZE * BLOCK_SIZE), Image.NEAREST)
        global image, draw
        image = img_resized
        draw = ImageDraw.Draw(image)
        redraw_canvas()


def save_image():
    file_path = filedialog.asksaveasfilename(defaultextension='.png',
                                              filetypes=[('PNG files', '*.png'),
                                                         ('All files', '*.*')])
    if file_path:
        resized_image = image.resize((GRID_SIZE, GRID_SIZE), Image.NEAREST)
        resized_image.save(file_path)


def setup_app():
    global root, image, draw, canvas, wheel_canvas, color_preview
    global brightness_slider, tool_buttons

    root = tk.Tk()
    root.title('Pixel Art Editor')
    root.resizable(False, False)

    image = Image.new('RGBA', (GRID_SIZE * BLOCK_SIZE, GRID_SIZE * BLOCK_SIZE), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)

    menu_bar = tk.Menu(root)
    root.config(menu=menu_bar)
    file_menu = tk.Menu(menu_bar, tearoff=0)
    menu_bar.add_cascade(label='File', menu=file_menu)
    file_menu.add_command(label='New', command=new_image)
    file_menu.add_command(label='Open', command=open_image)
    file_menu.add_command(label='Save', command=save_image)
    file_menu.add_separator()
    file_menu.add_command(label='Exit', command=root.destroy)

    main_frame = tk.Frame(root)
    main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

    sidebar = tk.Frame(main_frame, width=200)
    sidebar.grid(row=0, column=0, sticky='ns', padx=(0, 12))

    tk.Label(sidebar, text='Color Wheel', font=('TkDefaultFont', 10, 'bold')).pack(anchor='w', pady=(0, 4))

    wheel_size = wheel_radius * 2
    wheel_canvas = tk.Canvas(sidebar, width=wheel_size, height=wheel_size,
                              bg='white', highlightthickness=1,
                              highlightbackground='#cccccc', cursor='crosshair')
    wheel_canvas.pack()
    wheel_canvas.bind('<Button-1>', pick_wheel_color)
    wheel_canvas.bind('<B1-Motion>', pick_wheel_color)

    tk.Label(sidebar, text='Brightness', font=('TkDefaultFont', 9)).pack(anchor='w', pady=(8, 0))
    brightness_slider = tk.Scale(sidebar, from_=0.1, to=1.0, resolution=0.01,
                                  orient='horizontal', command=on_brightness_change,
                                  length=160, showvalue=False)
    brightness_slider.set(1.0)
    brightness_slider.pack(fill='x')

    tk.Label(sidebar, text='Current Color', font=('TkDefaultFont', 9)).pack(anchor='w', pady=(8, 2))
    color_preview = tk.Label(sidebar, bg='#000000', width=18, height=2,
                              relief='solid', bd=1)
    color_preview.pack(fill='x')

    tk.Label(sidebar, text='Tool', font=('TkDefaultFont', 10, 'bold')).pack(anchor='w', pady=(12, 4))
    tool_frame = tk.Frame(sidebar)
    tool_frame.pack(fill='x')
    tool_buttons = {}
    for tool_name in ['draw', 'erase', 'fill']:
        btn = tk.Button(tool_frame, text=tool_name.capitalize(), width=5,
                        command=lambda t=tool_name: set_tool(t))
        btn.pack(side='left', padx=2)
        tool_buttons[tool_name] = btn
    set_tool('draw')

    canvas_frame = tk.Frame(main_frame, bg='#f0f0f0')
    canvas_frame.grid(row=0, column=1, sticky='nsew')

    global canvas
    canvas = tk.Canvas(canvas_frame, bg='white',
                       highlightthickness=1, highlightbackground='#cccccc',
                       cursor='crosshair')
    canvas.pack()
    canvas.bind('<Button-1>', start_paint)
    canvas.bind('<B1-Motion>', on_motion)
    canvas.bind('<ButtonRelease-1>', stop_paint)

    redraw_canvas()
    draw_color_wheel()


if __name__ == '__main__':
    setup_app()
    root.mainloop()
