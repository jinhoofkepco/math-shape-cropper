from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import json
import math
import re
import sys
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import zipfile

from PIL import Image, ImageDraw, ImageOps, ImageTk

try:
    from .image_processing import (
        ProcessedCrop,
        process_crop,
        process_workbook_crop,
        sanitize_name,
        unique_path,
    )
    from .ocr_utils import normalize_answer_text, ocr_status, recognize_answer, recognize_problem_number
except ImportError:
    from image_processing import (  # type: ignore[no-redef]
        ProcessedCrop,
        process_crop,
        process_workbook_crop,
        sanitize_name,
        unique_path,
    )
    from ocr_utils import normalize_answer_text, ocr_status, recognize_answer, recognize_problem_number  # type: ignore[no-redef]


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
MODE_SHAPE = "shape"
MODE_WORKBOOK = "workbook"
MODE_FAST = "fast"
FAST_A = "A"
FAST_B = "B"
ERASER_BRUSH = "brush"
ERASER_AREA = "area"


@dataclass
class CropRecord:
    source_path: Path
    image: Image.Image
    original_bbox: tuple[int, int, int, int]
    refined_bbox: tuple[int, int, int, int]
    suffix_var: tk.StringVar
    problem_number_var: tk.StringVar
    answer_var: tk.StringVar
    ocr_note_var: tk.StringVar
    answer_bbox: tuple[int, int, int, int] | None = None
    force_max_width: bool = False
    photo: ImageTk.PhotoImage | None = None


@dataclass
class PageCropGroup:
    source_path: Path
    middle_name_var: tk.StringVar
    records: list[CropRecord]


@dataclass
class FastGuideLine:
    orientation: str
    coord: int
    side: str = "full"


class CropperApp(tk.Tk):
    def __init__(self, initial_folder: Path | None = None) -> None:
        super().__init__()
        self.title("수학 문제집 도형 크롭 보조 앱")
        self.geometry("1500x900")
        self.minsize(1100, 700)

        self.image_paths: list[Path] = []
        self.selected_folder: Path | None = None
        self.output_base: Path | None = None
        self.page_image: Image.Image | None = None
        self.page_photo: ImageTk.PhotoImage | None = None
        self.page_image_item: int | None = None
        self.current_path: Path | None = None
        self.display_scale = 1.0
        self.base_scale = 1.0
        self.zoom = 1.0
        self.image_offset = (0, 0)
        self.drag_start: tuple[int, int] | None = None
        self.selection_rect: int | None = None
        self.crop_records: list[CropRecord] = []
        self.page_groups: list[PageCropGroup] = []
        self.page_group_by_path: dict[Path, PageCropGroup] = {}
        self.last_subunit_name = ""

        self.mode_var = tk.StringVar(value=MODE_SHAPE)
        self.common_name_var = tk.StringVar()
        self.save_folder_var = tk.StringVar(value="cropped_shapes")
        self.status_var = tk.StringVar(value="폴더를 선택하세요.")
        self.crop_title_var = tk.StringVar(value="크롭한 도형")

        self.transparent_bg_var = tk.BooleanVar(value=False)
        self.fast_type_var = tk.StringVar(value=FAST_A)
        self.fast_lines: list[FastGuideLine] = []
        self.fast_vertical_line: FastGuideLine | None = None
        self.fast_drag_line: FastGuideLine | None = None
        self.fast_outer_bbox: tuple[int, int, int, int] | None = None
        self.fast_setting_outer = False
        self.fast_outer_start: tuple[int, int] | None = None
        self.fast_generated_signature: tuple[object, ...] | None = None
        self.recrop_target: CropRecord | None = None
        self.eraser_enabled = False
        self.eraser_sampling = False
        self.eraser_dragging = False
        self.eraser_color: tuple[int, int, int] | None = None
        self.eraser_sizes = (12, 26, 44)
        self.eraser_mode_var = tk.StringVar(value=ERASER_BRUSH)
        self.eraser_mode_buttons: dict[str, tk.Button] = {}
        self.eraser_size_var = tk.IntVar(value=26)
        self.eraser_size_buttons: list[tk.Canvas] = []
        self.eraser_last_point: tuple[int, int] | None = None
        self.eraser_area_start: tuple[int, int] | None = None
        self.eraser_area_rect: int | None = None

        self._configure_style()
        self._build_layout()
        self.on_mode_changed()
        self.bind("<Control-plus>", lambda _event: self.adjust_zoom(1.15))
        self.bind("<Control-equal>", lambda _event: self.adjust_zoom(1.15))
        self.bind("<Control-minus>", lambda _event: self.adjust_zoom(1 / 1.15))

        if initial_folder:
            self.load_folder(initial_folder)

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TButton", padding=(10, 6))
        style.configure("Panel.TFrame", background="#f7f8fa")
        style.configure("Toolbar.TFrame", background="#eef1f4")
        style.configure("Card.TFrame", background="#ffffff", borderwidth=1, relief="solid")
        style.configure("Muted.TLabel", foreground="#56606b")
        style.configure("Status.TLabel", foreground="#334155")

    def _build_layout(self) -> None:
        root = ttk.Frame(self)
        root.pack(fill=tk.BOTH, expand=True)

        panes = tk.PanedWindow(root, orient=tk.HORIZONTAL, sashwidth=6, bg="#d4dae2")
        panes.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(panes, width=230, style="Panel.TFrame")
        center = ttk.Frame(panes)
        right = ttk.Frame(panes, width=360, style="Panel.TFrame")
        panes.add(left, minsize=190, width=230)
        panes.add(center, minsize=520)
        panes.add(right, minsize=300, width=360)

        self._build_left_panel(left)
        self._build_center_panel(center)
        self._build_right_panel(right)

        status = ttk.Label(root, textvariable=self.status_var, style="Status.TLabel", anchor=tk.W)
        status.pack(fill=tk.X, padx=8, pady=(2, 6))

    def _build_left_panel(self, parent: ttk.Frame) -> None:
        header = ttk.Frame(parent, style="Panel.TFrame")
        header.pack(fill=tk.X, padx=10, pady=10)

        ttk.Button(header, text="폴더 선택", command=self.choose_folder).pack(fill=tk.X)
        ttk.Label(
            parent,
            text="문제집 사진",
            style="Muted.TLabel",
            anchor=tk.W,
        ).pack(fill=tk.X, padx=12, pady=(4, 4))

        list_frame = ttk.Frame(parent)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL)
        self.file_list = tk.Listbox(
            list_frame,
            activestyle="none",
            exportselection=False,
            selectmode=tk.BROWSE,
            yscrollcommand=scrollbar.set,
        )
        scrollbar.configure(command=self.file_list.yview)
        self.file_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.file_list.bind("<Button-1>", self.on_file_clicked)
        self.file_list.bind("<KeyRelease-Up>", self.on_file_selected)
        self.file_list.bind("<KeyRelease-Down>", self.on_file_selected)
        self.file_list.bind("<Return>", self.on_file_selected)

    def _build_center_panel(self, parent: ttk.Frame) -> None:
        toolbar = ttk.Frame(parent, style="Toolbar.TFrame")
        toolbar.pack(fill=tk.X)

        self.page_title_var = tk.StringVar(value="선택된 페이지 없음")
        ttk.Label(toolbar, textvariable=self.page_title_var, anchor=tk.W).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=12)
        ttk.Button(toolbar, text="-", width=3, command=lambda: self.adjust_zoom(1 / 1.15)).pack(side=tk.LEFT, padx=(0, 4), pady=6)
        ttk.Button(toolbar, text="맞춤", command=self.fit_page).pack(side=tk.LEFT, padx=4, pady=6)
        ttk.Button(toolbar, text="+", width=3, command=lambda: self.adjust_zoom(1.15)).pack(side=tk.LEFT, padx=(4, 6), pady=6)
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6, pady=4)
        self.fast_generate_button = ttk.Button(toolbar, text="선 크롭 생성", command=self.generate_fast_crops, state=tk.DISABLED)
        self.fast_generate_button.pack(side=tk.LEFT, padx=(0, 4), pady=6)
        self.fast_outer_button = ttk.Button(toolbar, text="외곽설정", command=self.start_fast_outer_setting, state=tk.DISABLED)
        self.fast_outer_button.pack(side=tk.LEFT, padx=(0, 4), pady=6)
        self.fast_clear_button = ttk.Button(toolbar, text="선 초기화", command=self.clear_fast_guides, state=tk.DISABLED)
        self.fast_clear_button.pack(side=tk.LEFT, padx=(0, 10), pady=6)

        self.page_canvas = tk.Canvas(parent, bg="#e8edf2", highlightthickness=0, cursor="crosshair")
        self.page_canvas.pack(fill=tk.BOTH, expand=True)
        self.page_canvas.bind("<Configure>", lambda _event: self.redraw_page())
        self.page_canvas.bind("<ButtonPress-1>", self.start_selection)
        self.page_canvas.bind("<B1-Motion>", self.update_selection)
        self.page_canvas.bind("<ButtonRelease-1>", self.finish_selection)
        self._build_eraser_panel()

    def _build_eraser_panel(self) -> None:
        self.eraser_panel = ttk.Frame(self.page_canvas, style="Panel.TFrame")
        self.eraser_icon = self._make_eraser_icon(active=False)
        self.eraser_icon_active = self._make_eraser_icon(active=True)
        self.eraser_brush_icon = self._make_eraser_mode_icon(ERASER_BRUSH, active=False)
        self.eraser_brush_icon_active = self._make_eraser_mode_icon(ERASER_BRUSH, active=True)
        self.eraser_area_icon = self._make_eraser_mode_icon(ERASER_AREA, active=False)
        self.eraser_area_icon_active = self._make_eraser_mode_icon(ERASER_AREA, active=True)
        self.eraser_button = tk.Button(
            self.eraser_panel,
            image=self.eraser_icon,
            width=28,
            height=28,
            relief=tk.RAISED,
            command=self.toggle_eraser,
            bg="#ffffff",
            activebackground="#e2e8f0",
            highlightthickness=1,
            highlightbackground="#cbd5e1",
        )
        self.eraser_button.pack(padx=4, pady=(4, 3))
        for mode, icon in ((ERASER_BRUSH, self.eraser_brush_icon), (ERASER_AREA, self.eraser_area_icon)):
            button = tk.Button(
                self.eraser_panel,
                image=icon,
                width=28,
                height=28,
                relief=tk.RAISED,
                command=lambda selected=mode: self.set_eraser_mode(selected),
                bg="#ffffff",
                activebackground="#e2e8f0",
                highlightthickness=1,
                highlightbackground="#cbd5e1",
            )
            button.pack(padx=4, pady=1)
            self.eraser_mode_buttons[mode] = button
        for size in self.eraser_sizes:
            button = tk.Canvas(self.eraser_panel, width=28, height=28, bg="#f7f8fa", highlightthickness=0, cursor="hand2")
            button.pack(padx=4, pady=1)
            button.bind("<Button-1>", lambda _event, selected=size: self.set_eraser_size(selected))
            self.eraser_size_buttons.append(button)
        self.eraser_color_swatch = tk.Canvas(self.eraser_panel, width=24, height=16, bg="#ffffff", highlightthickness=1)
        self.eraser_color_swatch.pack(padx=4, pady=(3, 4))
        self.update_eraser_mode_buttons()
        self.update_eraser_size_buttons()
        self.update_eraser_swatch()
        self.eraser_panel.place(relx=1.0, rely=0.5, anchor=tk.E, x=-3)

    def _make_eraser_icon(self, active: bool) -> ImageTk.PhotoImage:
        image = Image.new("RGBA", (22, 22), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        body = "#2563eb" if active else "#475569"
        edge = "#1e293b"
        paper = "#f8fafc"
        draw.polygon([(6, 14), (12, 7), (18, 12), (12, 19)], fill=body, outline=edge)
        draw.polygon([(4, 16), (6, 14), (12, 19), (10, 21)], fill=paper, outline=edge)
        draw.line((8, 12, 14, 17), fill="#e2e8f0", width=1)
        draw.line((4, 21, 18, 21), fill="#94a3b8", width=1)
        return ImageTk.PhotoImage(image)

    def _make_eraser_mode_icon(self, mode: str, active: bool) -> ImageTk.PhotoImage:
        image = Image.new("RGBA", (22, 22), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        color = "#2563eb" if active else "#475569"
        if mode == ERASER_AREA:
            draw.rectangle((4, 5, 18, 17), outline=color, width=2)
            draw.rectangle((7, 8, 15, 14), fill="#bfdbfe" if active else "#e2e8f0")
        else:
            draw.line((5, 15, 9, 11, 13, 12, 17, 7), fill=color, width=3)
            draw.ellipse((6, 12, 12, 18), fill="#bfdbfe" if active else "#e2e8f0", outline=color)
        return ImageTk.PhotoImage(image)

    def set_eraser_mode(self, mode: str) -> None:
        self.eraser_mode_var.set(mode)
        self.eraser_dragging = False
        self.eraser_last_point = None
        self.eraser_area_start = None
        if mode == ERASER_AREA:
            self.eraser_sampling = False
        if self.eraser_area_rect is not None:
            self.page_canvas.delete(self.eraser_area_rect)
            self.eraser_area_rect = None
        self.update_eraser_mode_buttons()
        if self.eraser_enabled:
            self.page_canvas.configure(cursor="tcross" if mode == ERASER_AREA else "dotbox")
            if mode == ERASER_AREA:
                self.status_var.set("영역 지우개: 드래그 시작점 색으로 선택 영역을 칠합니다.")

    def update_eraser_mode_buttons(self) -> None:
        for mode, button in self.eraser_mode_buttons.items():
            active = self.eraser_mode_var.get() == mode
            icon = (
                self.eraser_area_icon_active
                if mode == ERASER_AREA and active
                else self.eraser_area_icon
                if mode == ERASER_AREA
                else self.eraser_brush_icon_active
                if active
                else self.eraser_brush_icon
            )
            button.configure(image=icon, relief=tk.SUNKEN if active else tk.RAISED, bg="#dbeafe" if active else "#ffffff")

    def set_eraser_size(self, size: int) -> None:
        self.eraser_size_var.set(size)
        self.update_eraser_size_buttons()

    def update_eraser_size_buttons(self) -> None:
        display_radii = {self.eraser_sizes[0]: 4, self.eraser_sizes[1]: 7, self.eraser_sizes[2]: 10}
        selected = int(self.eraser_size_var.get())
        for size, button in zip(self.eraser_sizes, self.eraser_size_buttons):
            button.delete("all")
            outline = "#2563eb" if size == selected else "#64748b"
            fill = "#bfdbfe" if size == selected else "#ffffff"
            radius = display_radii.get(size, 7)
            center = 14
            button.create_oval(
                center - radius,
                center - radius,
                center + radius,
                center + radius,
                fill=fill,
                outline=outline,
                width=2 if size == selected else 1,
            )

    def _build_right_panel(self, parent: ttk.Frame) -> None:
        top = ttk.Frame(parent, style="Panel.TFrame")
        top.pack(fill=tk.X, padx=10, pady=10)

        ttk.Label(top, text="모드").pack(anchor=tk.W)
        mode_row = ttk.Frame(top, style="Panel.TFrame")
        mode_row.pack(fill=tk.X, pady=(2, 8))
        ttk.Radiobutton(
            mode_row,
            text="도형 저장",
            value=MODE_SHAPE,
            variable=self.mode_var,
            command=self.on_mode_changed,
        ).pack(side=tk.LEFT)
        ttk.Radiobutton(
            mode_row,
            text="워크북 생성",
            value=MODE_WORKBOOK,
            variable=self.mode_var,
            command=self.on_mode_changed,
        ).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Radiobutton(
            mode_row,
            text="fast",
            value=MODE_FAST,
            variable=self.mode_var,
            command=self.on_mode_changed,
        ).pack(side=tk.LEFT, padx=(8, 0))

        ttk.Label(top, text="fast 방식").pack(anchor=tk.W)
        fast_row = ttk.Frame(top, style="Panel.TFrame")
        fast_row.pack(fill=tk.X, pady=(2, 8))
        self.fast_a_button = ttk.Radiobutton(
            fast_row,
            text="A",
            value=FAST_A,
            variable=self.fast_type_var,
            command=self.on_fast_type_changed,
        )
        self.fast_a_button.pack(side=tk.LEFT)
        self.fast_b_button = ttk.Radiobutton(
            fast_row,
            text="B",
            value=FAST_B,
            variable=self.fast_type_var,
            command=self.on_fast_type_changed,
        )
        self.fast_b_button.pack(side=tk.LEFT, padx=(8, 0))

        ttk.Label(top, text="공통 이름 / 워크북 제목").pack(anchor=tk.W)
        ttk.Entry(top, textvariable=self.common_name_var).pack(fill=tk.X, pady=(2, 8))

        ttk.Label(top, text="저장 폴더 / ZIP 이름").pack(anchor=tk.W)
        save_row = ttk.Frame(top, style="Panel.TFrame")
        save_row.pack(fill=tk.X)
        ttk.Entry(save_row, textvariable=self.save_folder_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.save_button = ttk.Button(save_row, text="저장", command=self.save_all)
        self.save_button.pack(side=tk.LEFT, padx=(6, 0))

        base_row = ttk.Frame(top, style="Panel.TFrame")
        base_row.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(base_row, text="저장 위치", command=self.choose_output_base).pack(side=tk.LEFT)
        self.output_base_var = tk.StringVar(value="선택한 사진 폴더 기준")
        ttk.Label(base_row, textvariable=self.output_base_var, style="Muted.TLabel").pack(side=tk.LEFT, padx=(8, 0), fill=tk.X, expand=True)

        ttk.Checkbutton(
            top,
            text="배경 투명화 (PNG 알파)",
            variable=self.transparent_bg_var,
        ).pack(anchor=tk.W, pady=(8, 0))

        ttk.Label(parent, textvariable=self.crop_title_var, style="Muted.TLabel").pack(fill=tk.X, padx=12, pady=(2, 4))
        self.crop_canvas = tk.Canvas(parent, bg="#f7f8fa", highlightthickness=0)
        crop_scroll = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=self.crop_canvas.yview)
        self.crop_canvas.configure(yscrollcommand=crop_scroll.set)
        self.crop_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 0), pady=(0, 10))
        crop_scroll.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 10), pady=(0, 10))

        self.crop_inner = ttk.Frame(self.crop_canvas, style="Panel.TFrame")
        self.crop_window = self.crop_canvas.create_window((0, 0), window=self.crop_inner, anchor="nw")
        self.crop_inner.bind("<Configure>", self._update_crop_scroll_region)
        self.crop_canvas.bind("<Configure>", self._resize_crop_inner)
        for column in range(3):
            self.crop_inner.columnconfigure(column, weight=1, uniform="crop")

    def choose_folder(self) -> None:
        folder = filedialog.askdirectory(title="문제집 사진 폴더 선택")
        if folder:
            self.load_folder(Path(folder))

    def choose_output_base(self) -> None:
        folder = filedialog.askdirectory(title="저장 기준 폴더 선택")
        if folder:
            self.output_base = Path(folder)
            self.output_base_var.set(str(self.output_base))

    def on_mode_changed(self) -> None:
        fast_state = tk.NORMAL if self._is_fast_mode() else tk.DISABLED
        self.fast_generate_button.configure(state=fast_state)
        self.fast_outer_button.configure(state=fast_state)
        self.fast_clear_button.configure(state=fast_state)
        self.fast_a_button.configure(state=fast_state)
        self.fast_b_button.configure(state=fast_state)

        if self._is_fast_mode():
            self.save_button.configure(text="생성")
            self.crop_title_var.set("fast 문제")
            self.status_var.set(self._fast_status_text())
        elif self._is_workbook_mode():
            self.save_button.configure(text="생성")
            self.crop_title_var.set("워크북 문제")
            self.status_var.set(f"워크북 생성 모드입니다. {ocr_status()}")
        else:
            self.save_button.configure(text="저장")
            self.crop_title_var.set("크롭한 도형")
            self.status_var.set("도형 저장 모드입니다.")
        self._refresh_crop_grid()
        self.redraw_page()

    def on_fast_type_changed(self) -> None:
        self.clear_fast_guides()
        self.status_var.set(self._fast_status_text())

    def _is_workbook_mode(self) -> bool:
        return self.mode_var.get() == MODE_WORKBOOK

    def _is_fast_mode(self) -> bool:
        return self.mode_var.get() == MODE_FAST

    def _uses_workbook_export(self) -> bool:
        return self.mode_var.get() in {MODE_WORKBOOK, MODE_FAST}

    def _fast_status_text(self) -> str:
        if self.fast_type_var.get() == FAST_B:
            return "fast B: 첫 클릭은 세로 구분선, 이후 클릭은 좌/우 가로선입니다. 선은 드래그로 조정하세요."
        return "fast A: 클릭할 때마다 가로선이 생깁니다. 선은 드래그로 조정하세요."

    def load_folder(self, folder: Path) -> None:
        if not folder.exists():
            messagebox.showerror("폴더 없음", f"폴더를 찾을 수 없습니다.\n{folder}")
            return

        self.selected_folder = folder
        self.output_base = None
        self.output_base_var.set("선택한 사진 폴더 기준")
        self.crop_records.clear()
        self.page_groups.clear()
        self.page_group_by_path.clear()
        self.last_subunit_name = ""
        self.clear_fast_outer(redraw=False)
        self._refresh_crop_grid()
        self.image_paths = sorted(
            [path for path in folder.iterdir() if path.suffix.lower() in IMAGE_EXTENSIONS],
            key=lambda path: path.name.lower(),
        )
        self.file_list.delete(0, tk.END)
        for path in self.image_paths:
            self.file_list.insert(tk.END, path.name)

        self.status_var.set(f"{len(self.image_paths)}개 이미지 로드: {folder}")
        if self.image_paths:
            self.select_file_index(0)
        else:
            self.page_title_var.set("이미지가 없습니다.")
            self.page_image = None
            self.current_path = None
            self.redraw_page()

    def on_file_clicked(self, event: tk.Event) -> str:
        if not self.image_paths:
            return "break"
        index = self.file_list.nearest(event.y)
        item_box = self.file_list.bbox(index)
        if item_box is None:
            return "break"
        _x, y, _width, height = item_box
        if event.y < y or event.y > y + height:
            return "break"
        self.file_list.focus_set()
        self.select_file_index(index)
        return "break"

    def on_file_selected(self, _event: tk.Event) -> None:
        selection = self.file_list.curselection()
        if not selection:
            return
        self.select_file_index(selection[0])

    def select_file_index(self, index: int, *, load: bool = True) -> None:
        if index < 0 or index >= len(self.image_paths):
            return
        self.file_list.selection_clear(0, tk.END)
        self.file_list.selection_set(index)
        self.file_list.activate(index)
        self.file_list.see(index)
        if load and self.image_paths[index] != self.current_path:
            self.load_page(self.image_paths[index])

    def sync_file_selection_to_path(self, path: Path) -> None:
        try:
            index = self.image_paths.index(path)
        except ValueError:
            return
        self.select_file_index(index, load=False)

    def load_page(self, path: Path) -> None:
        if self.current_path is not None and path != self.current_path:
            self._auto_generate_fast_crops_before_page_change()
            self.deactivate_eraser()
            self.finalize_current_page_group()

        try:
            image = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
        except Exception as exc:
            messagebox.showerror("이미지 열기 실패", f"{path.name}\n{exc}")
            return

        self.page_image = image
        self.current_path = path
        self.sync_file_selection_to_path(path)
        self.zoom = 1.0
        self.clear_fast_guides(redraw=False)
        self.page_title_var.set(path.name)
        if not self.common_name_var.get().strip():
            self.common_name_var.set(path.stem)
        self.redraw_page()
        if self._is_fast_mode():
            self.status_var.set(self._fast_status_text())
        elif self._is_workbook_mode():
            self.status_var.set("문제 전체를 드래그하면 문제번호/빨간 답 OCR과 답 삭제를 시도합니다.")
        else:
            self.status_var.set("도형 영역을 드래그하면 오른쪽에 보정된 크롭이 추가됩니다.")

    def redraw_page(self) -> None:
        self.page_canvas.delete("all")
        self.selection_rect = None
        self.page_image_item = None
        self.eraser_area_rect = None
        canvas_width = self.page_canvas.winfo_width()
        canvas_height = self.page_canvas.winfo_height()
        if canvas_width <= 1 or canvas_height <= 1:
            return

        if self.page_image is None:
            self.page_canvas.create_text(
                canvas_width // 2,
                canvas_height // 2,
                text="왼쪽에서 문제집 사진 폴더를 선택하세요.",
                fill="#475569",
                font=("Malgun Gothic", 14),
            )
            return

        image_width, image_height = self.page_image.size
        self.base_scale = min((canvas_width - 24) / image_width, (canvas_height - 24) / image_height)
        self.base_scale = min(self.base_scale, 1.0)
        self.display_scale = max(0.05, self.base_scale * self.zoom)

        display_width = max(1, int(image_width * self.display_scale))
        display_height = max(1, int(image_height * self.display_scale))
        display_image = self.page_image.resize((display_width, display_height), Image.Resampling.LANCZOS)
        self.page_photo = ImageTk.PhotoImage(display_image)

        offset_x = max(12, (canvas_width - display_width) // 2)
        offset_y = max(12, (canvas_height - display_height) // 2)
        self.image_offset = (offset_x, offset_y)
        self.page_image_item = self.page_canvas.create_image(offset_x, offset_y, anchor=tk.NW, image=self.page_photo)
        self.page_canvas.create_rectangle(
            offset_x,
            offset_y,
            offset_x + display_width,
            offset_y + display_height,
            outline="#94a3b8",
        )
        if self.recrop_target is not None:
            self._draw_recrop_highlight(self.recrop_target)
        self._redraw_fast_guides()
        if hasattr(self, "eraser_panel"):
            self.eraser_panel.lift()

    def refresh_page_photo(self) -> None:
        if self.page_image is None or self.page_image_item is None:
            self.redraw_page()
            return
        display_width = max(1, int(self.page_image.width * self.display_scale))
        display_height = max(1, int(self.page_image.height * self.display_scale))
        display_image = self.page_image.resize((display_width, display_height), Image.Resampling.LANCZOS)
        self.page_photo = ImageTk.PhotoImage(display_image)
        self.page_canvas.itemconfigure(self.page_image_item, image=self.page_photo)
        if hasattr(self, "eraser_panel"):
            self.eraser_panel.lift()

    def fit_page(self) -> None:
        self.zoom = 1.0
        self.redraw_page()

    def adjust_zoom(self, factor: float) -> None:
        if self.page_image is None:
            return
        self.zoom = max(0.35, min(5.0, self.zoom * factor))
        self.redraw_page()

    def start_selection(self, event: tk.Event) -> None:
        if self.eraser_enabled:
            self.start_eraser(event)
            return
        if self._is_fast_mode():
            if self.fast_setting_outer:
                self.start_fast_outer(event)
                return
            self.start_fast_line(event)
            return
        if self.page_image is None or not self._point_in_display(event.x, event.y):
            self.drag_start = None
            return
        self.drag_start = self._clamp_display_point(event.x, event.y)
        self.selection_rect = self.page_canvas.create_rectangle(
            event.x,
            event.y,
            event.x,
            event.y,
            outline="#2563eb",
            width=2,
            dash=(4, 2),
        )

    def update_selection(self, event: tk.Event) -> None:
        if self.eraser_enabled:
            self.update_eraser(event)
            return
        if self._is_fast_mode():
            if self.fast_setting_outer:
                self.update_fast_outer(event)
                return
            self.update_fast_line(event)
            return
        if self.drag_start is None or self.selection_rect is None:
            return
        end_x, end_y = self._clamp_display_point(event.x, event.y)
        self.page_canvas.coords(self.selection_rect, self.drag_start[0], self.drag_start[1], end_x, end_y)

    def finish_selection(self, event: tk.Event) -> None:
        if self.eraser_enabled:
            self.finish_eraser(event)
            return
        if self._is_fast_mode():
            if self.fast_setting_outer:
                self.finish_fast_outer(event)
                return
            self.finish_fast_line(event)
            return
        if self.drag_start is None or self.selection_rect is None or self.page_image is None or self.current_path is None:
            return
        end_x, end_y = self._clamp_display_point(event.x, event.y)
        start_x, start_y = self.drag_start
        self.page_canvas.delete(self.selection_rect)
        self.selection_rect = None
        self.drag_start = None

        if abs(end_x - start_x) < 8 or abs(end_y - start_y) < 8:
            self.status_var.set("선택 영역이 너무 작습니다.")
            return

        bbox = self._display_bbox_to_image_bbox((start_x, start_y, end_x, end_y))
        make_transparent = self.transparent_bg_var.get()

        if self.recrop_target is not None:
            target = self.recrop_target
            self.recrop_target = None
            self.unbind("<Escape>")
            self._clear_recrop_highlight()
            try:
                processed = process_crop(self.page_image, bbox, refine_bounds=False, make_transparent=make_transparent)
            except Exception as exc:
                messagebox.showerror("재크롭 실패", str(exc))
                return
            target.image = processed.image
            target.original_bbox = processed.original_bbox
            target.refined_bbox = processed.refined_bbox
            target.photo = None
            self._refresh_crop_grid()
            self.status_var.set("재크롭 완료.")
            return

        try:
            if self._is_workbook_mode():
                processed = process_workbook_crop(self.page_image, bbox, make_transparent=make_transparent)
                problem_number = recognize_problem_number(processed.problem_number_image)
                answer = recognize_answer(processed.answer_image)
                self.add_crop(processed, self.current_path, problem_number=problem_number, answer=answer)
                return
            processed = process_crop(self.page_image, bbox, refine_bounds=False, make_transparent=make_transparent)
        except Exception as exc:
            messagebox.showerror("크롭 실패", str(exc))
            return
        self.add_crop(processed, self.current_path)

    def add_crop(
        self,
        processed: ProcessedCrop,
        source_path: Path,
        *,
        problem_number: str = "",
        answer: str = "",
        force_max_width: bool | None = None,
    ) -> None:
        group = self._get_page_group(source_path)
        suffix = str(len(group.records))
        note_parts: list[str] = []
        fast_a_crop = self._is_fast_mode() and self.fast_type_var.get() == FAST_A
        if force_max_width is None:
            force_max_width = fast_a_crop
        if self._uses_workbook_export():
            if fast_a_crop:
                note_parts.append("OCR 생략")
            else:
                if not problem_number:
                    note_parts.append("번호 확인 필요")
                if not answer:
                    note_parts.append("답 확인 필요")
                if getattr(processed, "red_pixel_count", 0) == 0:
                    note_parts.append("빨간 답 없음")
        record = CropRecord(
            source_path=source_path,
            image=processed.image,
            original_bbox=processed.original_bbox,
            refined_bbox=processed.refined_bbox,
            suffix_var=tk.StringVar(value=suffix),
            problem_number_var=tk.StringVar(value=problem_number),
            answer_var=tk.StringVar(value=answer),
            ocr_note_var=tk.StringVar(value=", ".join(note_parts)),
            answer_bbox=getattr(processed, "answer_bbox", None),
            force_max_width=force_max_width,
        )
        self.crop_records.append(record)
        group.records.append(record)
        self._refresh_crop_grid(scroll_to_bottom=True)
        if self._uses_workbook_export():
            label = f"문제 {problem_number or '?'} / 답 {answer or '?'}"
            prefix = "fast 문제" if self._is_fast_mode() else "워크북 문제"
            self.status_var.set(f"{prefix} 추가: {source_path.name} / {label}")
        else:
            self.status_var.set(f"크롭 추가: {source_path.name} / {suffix}")

    def finalize_current_page_group(self) -> None:
        if self.current_path is None:
            return
        group = self.page_group_by_path.get(self.current_path)
        if group is None:
            return

        changed = self._finalize_page_group(group)
        if changed:
            self._refresh_crop_grid()

    def finalize_all_page_groups(self) -> None:
        previous_middle = ""
        changed = False
        for group in self.page_groups:
            changed = self._finalize_page_group(group, previous_middle) or changed
            middle_name = group.middle_name_var.get().strip()
            if middle_name:
                previous_middle = middle_name
        if previous_middle:
            self.last_subunit_name = previous_middle
        if changed:
            self._refresh_crop_grid()

    def _finalize_page_group(self, group: PageCropGroup, previous_middle: str | None = None) -> bool:
        changed = False
        middle_name = group.middle_name_var.get().strip()
        if not middle_name:
            generated_middle = self._next_middle_name(previous_middle if previous_middle is not None else self.last_subunit_name)
            if generated_middle:
                group.middle_name_var.set(generated_middle)
                middle_name = generated_middle
                changed = True
        if middle_name:
            self.last_subunit_name = middle_name

        if self._uses_workbook_export():
            for index, record in enumerate(group.records):
                if not record.problem_number_var.get().strip():
                    record.problem_number_var.set(str(index))
                    changed = True
        return changed

    def _next_middle_name(self, previous_middle: str) -> str:
        previous_middle = previous_middle.strip()
        if not previous_middle:
            return ""
        last_match: re.Match[str] | None = None
        for match in re.finditer(r"\d+", previous_middle):
            last_match = match
        if last_match is None:
            return ""
        number_text = last_match.group(0)
        next_number = str(int(number_text) + 1)
        if number_text.startswith("0"):
            next_number = next_number.zfill(len(number_text))
        return f"{previous_middle[:last_match.start()]}{next_number}{previous_middle[last_match.end():]}"

    def _get_page_group(self, source_path: Path) -> PageCropGroup:
        group = self.page_group_by_path.get(source_path)
        if group is not None:
            return group
        group = PageCropGroup(
            source_path=source_path,
            middle_name_var=tk.StringVar(value=""),
            records=[],
        )
        self.page_group_by_path[source_path] = group
        self.page_groups.append(group)
        return group

    def _refresh_crop_grid(self, scroll_to_bottom: bool = False) -> None:
        for child in self.crop_inner.winfo_children():
            child.destroy()

        row = 0
        for group in self.page_groups:
            if not group.records:
                continue
            self._render_page_header(group, row)
            row += 1
            for index, record in enumerate(group.records):
                self._render_crop_card(record, row=row + index // 3, column=index % 3)
            row += max(1, math.ceil(len(group.records) / 3))

        self.crop_inner.update_idletasks()
        self._update_crop_scroll_region(None)
        if scroll_to_bottom:
            self.after_idle(lambda: self.crop_canvas.yview_moveto(1.0))

    def _render_page_header(self, group: PageCropGroup, row: int) -> None:
        header = ttk.Frame(self.crop_inner, style="Panel.TFrame")
        header.grid(row=row, column=0, columnspan=3, sticky="ew", padx=4, pady=(8 if row else 0, 4))
        header.columnconfigure(0, weight=1)
        if self._uses_workbook_export():
            ttk.Label(header, text="소단원").grid(row=0, column=0, sticky=tk.W, padx=(2, 6), pady=2)
            ttk.Entry(header, textvariable=group.middle_name_var).grid(row=0, column=1, sticky="ew", padx=2, pady=2)
            header.columnconfigure(1, weight=1)
            return
        self._numeric_name_control(header, group.middle_name_var, min_value=1, width=7, suffix_when_empty="_").grid(
            row=0, column=0, sticky="ew", padx=2, pady=2
        )

    def _render_crop_card(self, record: CropRecord, row: int, column: int) -> None:
        card = ttk.Frame(self.crop_inner, style="Card.TFrame")
        card.grid(row=row, column=column, sticky="nsew", padx=4, pady=4)
        card.columnconfigure(0, weight=1)

        preview = self._make_preview(record.image, max_width=94, max_height=82)
        record.photo = ImageTk.PhotoImage(preview)
        preview_label = ttk.Label(card, image=record.photo, background="#ffffff", cursor="hand2")
        preview_label.grid(row=0, column=0, padx=5, pady=(5, 3))
        preview_label.bind("<Button-1>", lambda _event, item=record: self._on_crop_card_click(item))
        preview_label.bind("<Button-3>", lambda _event, item=record: self._on_crop_card_click(item))

        if self._uses_workbook_export():
            fields = ttk.Frame(card)
            fields.grid(row=1, column=0, sticky="ew", padx=5, pady=(0, 6))
            fields.columnconfigure(1, weight=1)
            ttk.Label(fields, text="번호").grid(row=0, column=0, sticky=tk.W, padx=(0, 3))
            ttk.Entry(fields, textvariable=record.problem_number_var, width=7, justify=tk.CENTER).grid(
                row=0,
                column=1,
                sticky="ew",
            )
            ttk.Label(fields, text="답").grid(row=1, column=0, sticky=tk.W, padx=(0, 3), pady=(3, 0))
            ttk.Entry(fields, textvariable=record.answer_var, width=7, justify=tk.CENTER).grid(
                row=1,
                column=1,
                sticky="ew",
                pady=(3, 0),
            )
            if record.ocr_note_var.get():
                ttk.Label(card, textvariable=record.ocr_note_var, style="Muted.TLabel", wraplength=92).grid(
                    row=2,
                    column=0,
                    padx=5,
                    pady=(0, 5),
                )
            return

        self._numeric_name_control(card, record.suffix_var, min_value=0, width=4).grid(row=1, column=0, pady=(0, 6))

    def _numeric_name_control(
        self,
        parent: tk.Widget,
        value_var: tk.StringVar,
        min_value: int,
        width: int,
        suffix_when_empty: str = "",
    ) -> ttk.Frame:
        frame = ttk.Frame(parent)
        frame.columnconfigure(1, weight=1)
        ttk.Button(
            frame,
            text="<",
            width=2,
            command=lambda: self._step_numeric_name(value_var, -1, min_value, suffix_when_empty),
        ).grid(row=0, column=0, padx=(0, 2))
        ttk.Entry(frame, textvariable=value_var, width=width, justify=tk.CENTER).grid(row=0, column=1, sticky="ew")
        ttk.Button(
            frame,
            text=">",
            width=2,
            command=lambda: self._step_numeric_name(value_var, 1, min_value, suffix_when_empty),
        ).grid(row=0, column=2, padx=(2, 0))
        return frame

    def _step_numeric_name(self, value_var: tk.StringVar, delta: int, min_value: int, suffix_when_empty: str) -> None:
        value = value_var.get().strip()
        match = re.search(r"\d+", value)
        if match is None:
            value_var.set(f"{min_value}{suffix_when_empty}")
            return

        number = max(min_value, int(match.group()) + delta)
        value_var.set(f"{value[:match.start()]}{number}{value[match.end():]}")

    def delete_crop(self, record: CropRecord) -> None:
        if record in self.crop_records:
            self.crop_records.remove(record)
        group = self.page_group_by_path.get(record.source_path)
        if group is not None and record in group.records:
            group.records.remove(record)
        self.page_groups = [page_group for page_group in self.page_groups if page_group.records]
        self.page_group_by_path = {page_group.source_path: page_group for page_group in self.page_groups}
        self._refresh_crop_grid()
        self.status_var.set("크롭을 삭제했습니다.")

    def _on_crop_card_click(self, record: CropRecord) -> None:
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="재크롭", command=lambda: self._start_recrop(record))
        menu.add_separator()
        menu.add_command(label="삭제", command=lambda: self.delete_crop(record))
        try:
            menu.tk_popup(self.winfo_pointerx(), self.winfo_pointery())
        finally:
            menu.grab_release()

    def _start_recrop(self, record: CropRecord) -> None:
        if record.source_path != self.current_path:
            self.load_page(record.source_path)
        self.recrop_target = record
        self._draw_recrop_highlight(record)
        self.status_var.set("재크롭 모드: 새 영역을 드래그하세요. (ESC로 취소)")
        self.bind("<Escape>", self._cancel_recrop)

    def _cancel_recrop(self, _event: tk.Event | None = None) -> None:
        self.recrop_target = None
        self._clear_recrop_highlight()
        self.unbind("<Escape>")
        self.status_var.set("재크롭 취소됨.")

    def _draw_recrop_highlight(self, record: CropRecord) -> None:
        self._clear_recrop_highlight()
        if self.page_image is None or record.source_path != self.current_path:
            return
        bbox = record.refined_bbox
        offset_x, offset_y = self.image_offset
        x0 = offset_x + int(bbox[0] * self.display_scale)
        y0 = offset_y + int(bbox[1] * self.display_scale)
        x1 = offset_x + int(bbox[2] * self.display_scale)
        y1 = offset_y + int(bbox[3] * self.display_scale)
        self.page_canvas.create_rectangle(
            x0, y0, x1, y1,
            outline="#ef4444",
            width=2,
            dash=(6, 3),
            tags="recrop_highlight",
        )

    def _clear_recrop_highlight(self) -> None:
        self.page_canvas.delete("recrop_highlight")

    def toggle_eraser(self) -> None:
        self.eraser_enabled = not self.eraser_enabled
        self.eraser_dragging = False
        self.eraser_last_point = None
        self.eraser_area_start = None
        if self.eraser_area_rect is not None:
            self.page_canvas.delete(self.eraser_area_rect)
            self.eraser_area_rect = None
        self.eraser_sampling = self.eraser_enabled
        if self.eraser_enabled:
            self.eraser_button.configure(image=self.eraser_icon_active, relief=tk.SUNKEN, bg="#dbeafe")
            self.page_canvas.configure(cursor="tcross" if self.eraser_mode_var.get() == ERASER_AREA else "dotbox")
            if self.eraser_mode_var.get() == ERASER_AREA:
                self.eraser_sampling = False
                self.status_var.set("영역 지우개: 드래그 시작점 색으로 선택 영역을 칠합니다.")
            else:
                self.status_var.set("지우개: 지울 배경색으로 쓸 여백을 한 번 클릭하세요.")
        else:
            self.eraser_button.configure(image=self.eraser_icon, relief=tk.RAISED, bg="#ffffff")
            self.page_canvas.configure(cursor="crosshair")
            self.status_var.set("지우개 꺼짐.")

    def deactivate_eraser(self) -> None:
        if not self.eraser_enabled and not self.eraser_dragging and not self.eraser_sampling:
            return
        self.eraser_enabled = False
        self.eraser_sampling = False
        self.eraser_dragging = False
        self.eraser_last_point = None
        self.eraser_area_start = None
        if self.eraser_area_rect is not None:
            self.page_canvas.delete(self.eraser_area_rect)
            self.eraser_area_rect = None
        self.eraser_button.configure(image=self.eraser_icon, relief=tk.RAISED, bg="#ffffff")
        self.page_canvas.configure(cursor="crosshair")

    def on_eraser_size_changed(self, value: str) -> None:
        self.set_eraser_size(int(float(value)))

    def start_eraser(self, event: tk.Event) -> None:
        if self.page_image is None or not self._point_in_display(event.x, event.y):
            self.eraser_dragging = False
            return
        if self.eraser_mode_var.get() == ERASER_AREA:
            self.start_eraser_area(event)
            return
        if self.eraser_sampling:
            self.sample_eraser_color(event)
            self.eraser_sampling = False
            self.eraser_dragging = False
            return
        self.eraser_dragging = True
        self.eraser_last_point = self._display_point_to_image_point(event.x, event.y)
        self.apply_eraser(event)

    def update_eraser(self, event: tk.Event) -> None:
        if self.eraser_dragging and self.eraser_mode_var.get() == ERASER_AREA:
            self.update_eraser_area(event)
        elif self.eraser_dragging:
            self.apply_eraser(event)

    def finish_eraser(self, event: tk.Event) -> None:
        if self.eraser_dragging and self.eraser_mode_var.get() == ERASER_AREA:
            self.finish_eraser_area(event)
        self.eraser_dragging = False
        self.eraser_last_point = None
        self.eraser_area_start = None

    def start_eraser_area(self, event: tk.Event) -> None:
        self.eraser_dragging = True
        self.eraser_area_start = self._display_point_to_image_point(event.x, event.y)
        self.eraser_sampling = False
        self.eraser_color = self._average_image_color(self.eraser_area_start[0], self.eraser_area_start[1], radius=0)
        self.update_eraser_swatch()
        if self.eraser_area_rect is not None:
            self.page_canvas.delete(self.eraser_area_rect)
        x, y = self._clamp_display_point(event.x, event.y)
        self.eraser_area_rect = self.page_canvas.create_rectangle(
            x,
            y,
            x,
            y,
            outline="#2563eb",
            width=2,
            dash=(4, 2),
        )

    def update_eraser_area(self, event: tk.Event) -> None:
        if self.eraser_area_start is None or self.eraser_area_rect is None:
            return
        current = self._display_point_to_image_point(event.x, event.y)
        display_bbox = self._image_bbox_to_display_bbox(self._bbox_from_points(self.eraser_area_start, current))
        self.page_canvas.coords(self.eraser_area_rect, *display_bbox)

    def finish_eraser_area(self, event: tk.Event) -> None:
        if self.eraser_area_start is None:
            return
        current = self._display_point_to_image_point(event.x, event.y)
        self._erase_image_bbox(self._bbox_from_points(self.eraser_area_start, current))
        if self.eraser_area_rect is not None:
            self.page_canvas.delete(self.eraser_area_rect)
            self.eraser_area_rect = None
        self.refresh_page_photo()

    def sample_eraser_color(self, event: tk.Event) -> None:
        if self.page_image is None:
            return
        image_x, image_y = self._display_point_to_image_point(event.x, event.y)
        self.eraser_color = self._average_image_color(image_x, image_y, radius=4)
        self.update_eraser_swatch()
        self.status_var.set(f"지우개 색상 선택됨: RGB{self.eraser_color}. 드래그하면 지워집니다.")

    def apply_eraser(self, event: tk.Event) -> None:
        if self.page_image is None or self.eraser_color is None:
            return
        current = self._display_point_to_image_point(event.x, event.y)
        previous = self.eraser_last_point or current
        self._draw_eraser_stroke(previous, current)
        self.eraser_last_point = current
        self.refresh_page_photo()

    def _draw_eraser_stroke(self, previous: tuple[int, int], current: tuple[int, int]) -> None:
        if self.page_image is None or self.eraser_color is None:
            return
        diameter = max(2, int(self.eraser_size_var.get()))
        radius = max(1, diameter // 2)
        draw = ImageDraw.Draw(self.page_image)
        draw.line((previous[0], previous[1], current[0], current[1]), fill=self.eraser_color, width=diameter)
        distance = math.hypot(current[0] - previous[0], current[1] - previous[1])
        step = max(1, radius // 3)
        steps = max(1, int(math.ceil(distance / step)))
        for step_index in range(steps + 1):
            ratio = step_index / steps
            image_x = int(round(previous[0] + (current[0] - previous[0]) * ratio))
            image_y = int(round(previous[1] + (current[1] - previous[1]) * ratio))
            draw.ellipse(
                (image_x - radius, image_y - radius, image_x + radius, image_y + radius),
                fill=self.eraser_color,
            )

    def _erase_image_bbox(self, bbox: tuple[int, int, int, int]) -> None:
        if self.page_image is None or self.eraser_color is None:
            return
        left, top, right, bottom = bbox
        if right <= left or bottom <= top:
            return
        draw = ImageDraw.Draw(self.page_image)
        draw.rectangle((left, top, right, bottom), fill=self.eraser_color)

    def _bbox_from_points(self, start: tuple[int, int], end: tuple[int, int]) -> tuple[int, int, int, int]:
        if self.page_image is None:
            left, right = sorted((start[0], end[0]))
            top, bottom = sorted((start[1], end[1]))
            return left, top, right, bottom
        left, right = sorted((start[0], end[0]))
        top, bottom = sorted((start[1], end[1]))
        return (
            max(0, min(self.page_image.width, left)),
            max(0, min(self.page_image.height, top)),
            max(0, min(self.page_image.width, right)),
            max(0, min(self.page_image.height, bottom)),
        )

    def _average_image_color(self, image_x: int, image_y: int, radius: int) -> tuple[int, int, int]:
        if self.page_image is None:
            return (255, 255, 255)
        image_x = max(0, min(self.page_image.width - 1, image_x))
        image_y = max(0, min(self.page_image.height - 1, image_y))
        left = max(0, image_x - radius)
        top = max(0, image_y - radius)
        right = min(self.page_image.width - 1, image_x + radius)
        bottom = min(self.page_image.height - 1, image_y + radius)
        red_total = 0
        green_total = 0
        blue_total = 0
        count = 0
        for y in range(top, bottom + 1):
            for x in range(left, right + 1):
                pixel = self.page_image.getpixel((x, y))
                if isinstance(pixel, int):
                    red, green, blue = pixel, pixel, pixel
                else:
                    red, green, blue = pixel[:3]
                red_total += red
                green_total += green
                blue_total += blue
                count += 1
        if count == 0:
            return (255, 255, 255)
        return (red_total // count, green_total // count, blue_total // count)

    def update_eraser_swatch(self) -> None:
        self.eraser_color_swatch.delete("all")
        color = self.eraser_color or (255, 255, 255)
        hex_color = f"#{color[0]:02x}{color[1]:02x}{color[2]:02x}"
        self.eraser_color_swatch.create_rectangle(0, 0, 30, 18, fill=hex_color, outline="#334155")

    def start_fast_line(self, event: tk.Event) -> None:
        if self.page_image is None or not self._point_in_display(event.x, event.y):
            self.fast_drag_line = None
            return

        hit = self._find_fast_line_at_display(event.x, event.y)
        if hit is not None:
            self.fast_drag_line = hit
            return

        image_x, image_y = self._display_point_to_image_point(event.x, event.y)
        outer_left, outer_top, outer_right, outer_bottom = self._fast_outer_limits()
        image_x = max(outer_left + 1, min(outer_right - 1, image_x))
        image_y = max(outer_top + 1, min(outer_bottom - 1, image_y))
        if self.fast_type_var.get() == FAST_B:
            if self.fast_vertical_line is None:
                self.fast_vertical_line = FastGuideLine("v", image_x, "full")
                self.fast_drag_line = self.fast_vertical_line
                self.status_var.set("fast B: 세로 구분선을 만들었습니다. 좌/우 영역에 가로선을 추가하세요.")
            else:
                side = "left" if image_x < self.fast_vertical_line.coord else "right"
                line = FastGuideLine("h", image_y, side)
                self.fast_lines.append(line)
                self.fast_drag_line = line
                self.status_var.set(f"fast B: {side} 가로선을 추가했습니다.")
        else:
            line = FastGuideLine("h", image_y, "full")
            self.fast_lines.append(line)
            self.fast_drag_line = line
            self.status_var.set("fast A: 가로선을 추가했습니다.")
        self.fast_generated_signature = None
        self._redraw_fast_guides()

    def start_fast_outer_setting(self) -> None:
        if not self._is_fast_mode():
            return
        if self.page_image is None:
            self.status_var.set("fast 외곽설정: 먼저 페이지 이미지를 선택하세요.")
            return
        self.deactivate_eraser()
        self.fast_setting_outer = True
        self.fast_outer_start = None
        self.fast_drag_line = None
        self.page_canvas.delete("fast_outer_temp")
        self.status_var.set("fast 외곽설정: 크롭할 전체 외곽 사각형을 드래그하세요.")

    def start_fast_outer(self, event: tk.Event) -> None:
        if self.page_image is None or not self._point_in_display(event.x, event.y):
            self.fast_outer_start = None
            return
        self.fast_outer_start = self._display_point_to_image_point(event.x, event.y)
        self.page_canvas.delete("fast_outer_temp")
        self.page_canvas.create_rectangle(
            event.x,
            event.y,
            event.x,
            event.y,
            outline="#16a34a",
            width=3,
            dash=(5, 2),
            tags="fast_outer_temp",
        )

    def update_fast_outer(self, event: tk.Event) -> None:
        if self.fast_outer_start is None:
            return
        x, y = self._clamp_display_point(event.x, event.y)
        start_x, start_y = self.fast_outer_start
        offset_x, offset_y = self.image_offset
        display_start_x = offset_x + int(start_x * self.display_scale)
        display_start_y = offset_y + int(start_y * self.display_scale)
        self.page_canvas.coords("fast_outer_temp", display_start_x, display_start_y, x, y)

    def finish_fast_outer(self, event: tk.Event) -> None:
        if self.fast_outer_start is None or self.page_image is None:
            return
        end_x, end_y = self._display_point_to_image_point(event.x, event.y)
        start_x, start_y = self.fast_outer_start
        left, right = sorted((start_x, end_x))
        top, bottom = sorted((start_y, end_y))
        self.page_canvas.delete("fast_outer_temp")
        self.fast_outer_start = None
        self.fast_setting_outer = False
        if right - left < 20 or bottom - top < 20:
            self.status_var.set("fast 외곽설정: 외곽 영역이 너무 작습니다.")
            return
        self.fast_outer_bbox = (
            max(0, min(self.page_image.width, left)),
            max(0, min(self.page_image.height, top)),
            max(0, min(self.page_image.width, right)),
            max(0, min(self.page_image.height, bottom)),
        )
        self.fast_generated_signature = None
        self._redraw_fast_guides()
        self.status_var.set("fast 외곽설정 완료: 이후 선 크롭은 외곽 안에서만 생성됩니다.")

    def update_fast_line(self, event: tk.Event) -> None:
        if self.fast_drag_line is None or self.page_image is None:
            return
        image_x, image_y = self._display_point_to_image_point(event.x, event.y)
        outer_left, outer_top, outer_right, outer_bottom = self._fast_outer_limits()
        if self.fast_drag_line.orientation == "v":
            self.fast_drag_line.coord = max(outer_left + 1, min(outer_right - 1, image_x))
        else:
            self.fast_drag_line.coord = max(outer_top + 1, min(outer_bottom - 1, image_y))
        self.fast_generated_signature = None
        self._redraw_fast_guides()

    def finish_fast_line(self, _event: tk.Event) -> None:
        self.fast_drag_line = None

    def clear_fast_guides(self, redraw: bool = True) -> None:
        self.fast_lines.clear()
        self.fast_vertical_line = None
        self.fast_drag_line = None
        self.fast_setting_outer = False
        self.fast_outer_start = None
        self.fast_generated_signature = None
        if hasattr(self, "page_canvas"):
            self.page_canvas.delete("fast_guide")
            self.page_canvas.delete("fast_outer_temp")
        if redraw:
            self.redraw_page()

    def clear_fast_outer(self, redraw: bool = True) -> None:
        self.fast_outer_bbox = None
        self.fast_setting_outer = False
        self.fast_outer_start = None
        self.fast_generated_signature = None
        if hasattr(self, "page_canvas"):
            self.page_canvas.delete("fast_outer_temp")
        if redraw:
            self.redraw_page()

    def generate_fast_crops(self) -> None:
        if not self._is_fast_mode() or self.page_image is None or self.current_path is None:
            return
        bboxes = self._fast_bboxes()
        if not bboxes:
            self.status_var.set("fast: 생성할 크롭 영역이 없습니다. 선을 먼저 추가하세요.")
            return

        make_transparent = self.transparent_bg_var.get()
        added = 0
        skip_ocr = self.fast_type_var.get() == FAST_A
        for bbox in bboxes:
            try:
                if skip_ocr:
                    processed = process_crop(self.page_image, bbox, refine_bounds=False, make_transparent=make_transparent)
                    problem_number = ""
                    answer = ""
                else:
                    processed = process_workbook_crop(self.page_image, bbox, make_transparent=make_transparent)
                    problem_number = recognize_problem_number(processed.problem_number_image)
                    answer = recognize_answer(processed.answer_image)
            except Exception:
                continue
            self.add_crop(
                processed,
                self.current_path,
                problem_number=problem_number,
                answer=answer,
                force_max_width=skip_ocr,
            )
            added += 1
        if added:
            self.fast_generated_signature = self._fast_generation_signature()
        self.status_var.set(f"fast 크롭 생성 완료: {added}개 영역")

    def _auto_generate_fast_crops_before_page_change(self) -> None:
        if not self._is_fast_mode() or self.page_image is None or self.current_path is None:
            return
        bboxes = self._fast_bboxes()
        if not bboxes:
            return
        signature = self._fast_generation_signature()
        if signature == self.fast_generated_signature:
            return
        self.generate_fast_crops()

    def _fast_generation_signature(self) -> tuple[object, ...]:
        lines = tuple(sorted((line.orientation, line.coord, line.side) for line in self.fast_lines))
        vertical = None
        if self.fast_vertical_line is not None:
            vertical = (
                self.fast_vertical_line.orientation,
                self.fast_vertical_line.coord,
                self.fast_vertical_line.side,
            )
        return (
            str(self.current_path) if self.current_path is not None else "",
            self.fast_type_var.get(),
            self.fast_outer_bbox,
            vertical,
            lines,
            bool(self.transparent_bg_var.get()),
        )

    def _fast_bboxes(self) -> list[tuple[int, int, int, int]]:
        if self.page_image is None:
            return []
        width, height = self.page_image.size
        outer_left, outer_top, outer_right, outer_bottom = self._fast_outer_limits()
        outer_width = outer_right - outer_left
        outer_height = outer_bottom - outer_top
        min_width = max(20, outer_width // 30)
        min_height = max(20, outer_height // 40)

        if self.fast_type_var.get() == FAST_B:
            if self.fast_vertical_line is None or not self.fast_lines:
                return []
            split_x = max(outer_left + 1, min(outer_right - 1, self.fast_vertical_line.coord))
            result: list[tuple[int, int, int, int]] = []
            for side, x0, x1 in (("left", outer_left, split_x), ("right", split_x, outer_right)):
                side_lines = sorted(
                    max(outer_top + 1, min(outer_bottom - 1, line.coord))
                    for line in self.fast_lines
                    if line.side == side
                )
                for y0, y1 in self._ranges_from_lines(side_lines, outer_top, outer_bottom):
                    if x1 - x0 >= min_width and y1 - y0 >= min_height:
                        result.append((x0, y0, x1, y1))
            return result

        if not self.fast_lines:
            return []
        full_lines = sorted(max(outer_top + 1, min(outer_bottom - 1, line.coord)) for line in self.fast_lines)
        return [
            (outer_left, y0, outer_right, y1)
            for y0, y1 in self._ranges_from_lines(full_lines, outer_top, outer_bottom)
            if y1 - y0 >= min_height
        ]

    def _ranges_from_lines(self, lines: list[int], start: int, end: int) -> list[tuple[int, int]]:
        boundaries = [start]
        for coord in sorted(set(lines)):
            if start < coord < end:
                boundaries.append(coord)
        boundaries.append(end)
        return [(boundaries[index], boundaries[index + 1]) for index in range(len(boundaries) - 1)]

    def _redraw_fast_guides(self) -> None:
        self.page_canvas.delete("fast_guide")
        if not self._is_fast_mode() or self.page_image is None:
            return
        self._draw_fast_outer()
        if self.fast_vertical_line is not None:
            self._draw_fast_line(self.fast_vertical_line)
        for line in self.fast_lines:
            self._draw_fast_line(line)

    def _draw_fast_outer(self) -> None:
        if self.fast_outer_bbox is None:
            return
        x0, y0, x1, y1 = self._image_bbox_to_display_bbox(self._fast_outer_limits())
        self.page_canvas.create_rectangle(
            x0,
            y0,
            x1,
            y1,
            outline="#16a34a",
            width=3,
            dash=(5, 2),
            tags="fast_guide",
        )

    def _draw_fast_line(self, line: FastGuideLine) -> None:
        geometry = self._fast_line_display_geometry(line)
        if geometry is None:
            return
        x0, y0, x1, y1 = geometry
        color = "#dc2626" if line.orientation == "v" else ("#2563eb" if line.side != "right" else "#f97316")
        self.page_canvas.create_line(
            x0,
            y0,
            x1,
            y1,
            fill=color,
            width=3,
            tags="fast_guide",
        )

    def _fast_line_display_geometry(self, line: FastGuideLine) -> tuple[int, int, int, int] | None:
        if self.page_image is None:
            return None
        offset_x, offset_y = self.image_offset
        outer_left, outer_top, outer_right, outer_bottom = self._fast_outer_limits()
        outer_display_left = offset_x + int(outer_left * self.display_scale)
        outer_display_top = offset_y + int(outer_top * self.display_scale)
        outer_display_right = offset_x + int(outer_right * self.display_scale)
        outer_display_bottom = offset_y + int(outer_bottom * self.display_scale)
        if line.orientation == "v":
            coord = max(outer_left + 1, min(outer_right - 1, line.coord))
            x = offset_x + int(coord * self.display_scale)
            return x, outer_display_top, x, outer_display_bottom

        coord = max(outer_top + 1, min(outer_bottom - 1, line.coord))
        y = offset_y + int(coord * self.display_scale)
        x0 = outer_display_left
        x1 = outer_display_right
        if self.fast_type_var.get() == FAST_B and self.fast_vertical_line is not None:
            split_coord = max(outer_left + 1, min(outer_right - 1, self.fast_vertical_line.coord))
            split_x = offset_x + int(split_coord * self.display_scale)
            if line.side == "left":
                x1 = split_x
            elif line.side == "right":
                x0 = split_x
        return x0, y, x1, y

    def _fast_outer_limits(self) -> tuple[int, int, int, int]:
        if self.page_image is None:
            return 0, 0, 0, 0
        if self.fast_outer_bbox is None:
            return 0, 0, self.page_image.width, self.page_image.height
        left, top, right, bottom = self.fast_outer_bbox
        left = max(0, min(self.page_image.width - 1, left))
        right = max(left + 1, min(self.page_image.width, right))
        top = max(0, min(self.page_image.height - 1, top))
        bottom = max(top + 1, min(self.page_image.height, bottom))
        return left, top, right, bottom

    def _find_fast_line_at_display(self, x: int, y: int) -> FastGuideLine | None:
        candidates: list[tuple[int, FastGuideLine]] = []
        all_lines = list(self.fast_lines)
        if self.fast_vertical_line is not None:
            all_lines.append(self.fast_vertical_line)
        for line in all_lines:
            geometry = self._fast_line_display_geometry(line)
            if geometry is None:
                continue
            x0, y0, x1, y1 = geometry
            if line.orientation == "v":
                distance = abs(x - x0)
                if distance <= 7 and min(y0, y1) - 6 <= y <= max(y0, y1) + 6:
                    candidates.append((distance, line))
            else:
                distance = abs(y - y0)
                if distance <= 7 and min(x0, x1) - 6 <= x <= max(x0, x1) + 6:
                    candidates.append((distance, line))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1]

    def _display_point_to_image_point(self, x: int, y: int) -> tuple[int, int]:
        clamped_x, clamped_y = self._clamp_display_point(x, y)
        offset_x, offset_y = self.image_offset
        image_x = int(round((clamped_x - offset_x) / self.display_scale))
        image_y = int(round((clamped_y - offset_y) / self.display_scale))
        if self.page_image is None:
            return image_x, image_y
        return (
            max(0, min(self.page_image.width, image_x)),
            max(0, min(self.page_image.height, image_y)),
        )

    def save_all(self) -> None:
        if not self.crop_records:
            messagebox.showwarning("저장할 크롭 없음", "먼저 영역을 크롭하세요.")
            return

        self.finalize_all_page_groups()
        if self._uses_workbook_export():
            self.create_workbook_zip()
            return

        self.save_crops()

    def save_crops(self) -> None:
        base = self.output_base or self.selected_folder or Path.cwd()
        folder_name = sanitize_name(self.save_folder_var.get(), "cropped_shapes")
        output_dir = base / folder_name
        output_dir.mkdir(parents=True, exist_ok=True)

        common = sanitize_name(self.common_name_var.get(), "shape")
        manifest: list[dict[str, object]] = []
        saved_count = 0

        for page_index, group in enumerate(self.page_groups, start=1):
            middle = sanitize_name(group.middle_name_var.get(), group.source_path.stem)
            for problem_index, record in enumerate(group.records):
                file_name = self._page_problem_file_name(page_index, problem_index)
                output_path = unique_path(output_dir / file_name)
                record.image.save(output_path)
                saved_count += 1
                manifest.append(
                    {
                        "file": output_path.name,
                        "source": str(record.source_path),
                        "page_name": middle,
                        "page_index": page_index,
                        "problem_index": problem_index,
                        "original_bbox": record.original_bbox,
                        "refined_bbox": record.refined_bbox,
                    }
                )

        manifest_path = unique_path(output_dir / f"{common}_manifest.json")
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        self.status_var.set(f"{saved_count}개 저장 완료: {output_dir}")
        messagebox.showinfo("저장 완료", f"{saved_count}개 이미지를 저장했습니다.\n{output_dir}")

    def create_workbook_zip(self) -> None:
        base = self.output_base or self.selected_folder or Path.cwd()
        export_name = sanitize_name(self.save_folder_var.get(), "workbook_export")
        output_dir = base / export_name
        output_dir.mkdir(parents=True, exist_ok=True)

        workbook_title = self.common_name_var.get().strip() or export_name
        workbook_id = self._slugify(workbook_title, "workbook")
        zip_path = unique_path(output_dir / f"{workbook_id}.zip")

        chapters: list[dict[str, object]] = []
        chapter_by_subunit: dict[str, str] = {}
        record_items: list[tuple[str, str, int, int, CropRecord]] = []
        for page_index, group in enumerate(self.page_groups, start=1):
            if not group.records:
                continue
            subunit_name = group.middle_name_var.get().strip() or "기본 진도"
            chapter_id = chapter_by_subunit.get(subunit_name)
            if chapter_id is None:
                chapter_id = f"{workbook_id}-ch{len(chapters) + 1:02d}"
                chapter_by_subunit[subunit_name] = chapter_id
                chapters.append(
                    {
                        "chapterId": chapter_id,
                        "title": subunit_name,
                        "orderIndex": len(chapters) + 1,
                    }
                )
            for problem_index, record in enumerate(group.records):
                record_items.append((chapter_id, subunit_name, page_index, problem_index, record))

        problems: list[dict[str, object]] = []
        manifest: list[dict[str, object]] = []
        used_image_names: set[str] = set()

        for index, (chapter_id, subunit_name, page_index, problem_index, record) in enumerate(record_items, start=1):
            problem_number = record.problem_number_var.get().strip() or str(problem_index)
            answer = normalize_answer_text(record.answer_var.get())
            problem_key = f"{page_index}-{problem_index}"
            problem_id = f"{workbook_id}-p{index:03d}"
            image_name = self._unique_zip_image_name(problem_key, used_image_names)
            answer_field_id = f"{problem_id}-answer"
            answer_type = self._answer_type(answer)
            problem_label = f"문제 {problem_number}" if problem_number else f"문제 {index}"

            problems.append(
                {
                    "problemId": problem_id,
                    "chapterId": chapter_id,
                    "problemType": "IMAGE_BASED",
                    "questionText": problem_label,
                    "imagePath": f"images/{image_name}",
                    "imageDisplayJson": self._image_display_json(record),
                    "orderIndex": index,
                    "answerFields": [
                        {
                            "answerFieldId": answer_field_id,
                            "label": "답",
                            "fieldType": "FRACTION" if answer_type == "FRACTION" else "NUMBER",
                            "orderIndex": 1,
                            "required": True,
                        }
                    ],
                    "answerRules": [
                        {
                            "answerRuleId": f"{problem_id}-rule",
                            "answerFieldId": answer_field_id,
                            "answerType": answer_type,
                            "correctAnswerRaw": answer,
                            "normalizedAnswer": answer,
                            "unitType": "NONE",
                        }
                    ],
                }
            )
            manifest.append(
                {
                    "problemNumber": problem_number,
                    "answer": answer,
                    "subunit": subunit_name,
                    "chapterId": chapter_id,
                    "source": str(record.source_path),
                    "imagePath": f"images/{image_name}",
                    "pageIndex": page_index,
                    "problemIndex": problem_index,
                    "original_bbox": record.original_bbox,
                    "answer_bbox": record.answer_bbox,
                    "ocr_note": record.ocr_note_var.get(),
                    "forceMaxWidth": record.force_max_width,
                }
            )

        workbook_json = {
            "workbook": {
                "workbookId": workbook_id,
                "title": workbook_title,
                "description": "math-shape-cropper 워크북 생성 모드에서 만든 문제집",
                "grade": "",
                "version": 1,
            },
            "chapters": chapters,
            "problems": problems,
        }

        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("workbook.json", json.dumps(workbook_json, ensure_ascii=False, indent=2))
            archive.writestr("cropper_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            for problem, (_chapter_id, _subunit_name, _page_index, _problem_index, record) in zip(problems, record_items):
                image_path = str(problem["imagePath"])
                buffer = BytesIO()
                record.image.save(buffer, format="PNG")
                archive.writestr(image_path, buffer.getvalue())

        self.status_var.set(f"{len(problems)}개 문제 ZIP 생성 완료: {zip_path}")
        messagebox.showinfo("생성 완료", f"{len(problems)}개 문제를 워크북 ZIP으로 생성했습니다.\n{zip_path}")

    def _page_problem_file_name(self, page_index: int, problem_index: int) -> str:
        return f"{page_index}-{problem_index}.png"

    def _build_output_file_name(self, common: str, middle: str, suffix: str) -> str:
        parts: list[str] = []
        if common:
            parts.append(common.rstrip("_"))
        if middle and suffix:
            parts.append(f"{middle}{suffix}" if middle.endswith("_") else f"{middle}_{suffix}")
        elif middle:
            parts.append(middle.rstrip("_"))
        elif suffix:
            parts.append(suffix)
        return "_".join(part for part in parts if part) + ".png"

    def _slugify(self, value: str, fallback: str) -> str:
        safe = sanitize_name(value, fallback).lower()
        safe = re.sub(r"[^0-9a-zA-Z]+", "-", safe).strip("-")
        return safe or fallback

    def _unique_zip_image_name(self, problem_key: str, used_names: set[str]) -> str:
        base_name = f"{problem_key}.png"
        if base_name not in used_names:
            used_names.add(base_name)
            return base_name
        index = 2
        while True:
            candidate = f"{problem_key}-{index}.png"
            if candidate not in used_names:
                used_names.add(candidate)
                return candidate
            index += 1

    def _answer_type(self, answer: str) -> str:
        if "/" in answer:
            return "FRACTION"
        if "." in answer:
            return "DECIMAL"
        return "INTEGER"

    def _image_height_dp(self, image: Image.Image) -> int:
        if image.width <= 0:
            return 420
        scaled_height = int(320 * image.height / image.width)
        return max(220, min(720, scaled_height))

    def _image_display_json(self, record: CropRecord) -> dict[str, object]:
        width_fraction = 1.0 if record.force_max_width else 1
        return {
            "heightDp": self._image_height_dp(record.image),
            "widthFraction": width_fraction,
            "align": "center",
            "placement": "aboveText",
        }

    def _point_in_display(self, x: int, y: int) -> bool:
        if self.page_image is None:
            return False
        offset_x, offset_y = self.image_offset
        width = int(self.page_image.width * self.display_scale)
        height = int(self.page_image.height * self.display_scale)
        return offset_x <= x <= offset_x + width and offset_y <= y <= offset_y + height

    def _clamp_display_point(self, x: int, y: int) -> tuple[int, int]:
        if self.page_image is None:
            return x, y
        offset_x, offset_y = self.image_offset
        width = int(self.page_image.width * self.display_scale)
        height = int(self.page_image.height * self.display_scale)
        return (
            max(offset_x, min(offset_x + width, x)),
            max(offset_y, min(offset_y + height, y)),
        )

    def _display_bbox_to_image_bbox(self, bbox: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        if self.page_image is None:
            return 0, 0, 0, 0
        offset_x, offset_y = self.image_offset
        x0, y0, x1, y1 = bbox
        left = math.floor((min(x0, x1) - offset_x) / self.display_scale)
        top = math.floor((min(y0, y1) - offset_y) / self.display_scale)
        right = math.ceil((max(x0, x1) - offset_x) / self.display_scale)
        bottom = math.ceil((max(y0, y1) - offset_y) / self.display_scale)
        return (
            max(0, min(self.page_image.width, left)),
            max(0, min(self.page_image.height, top)),
            max(0, min(self.page_image.width, right)),
            max(0, min(self.page_image.height, bottom)),
        )

    def _image_bbox_to_display_bbox(self, bbox: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        offset_x, offset_y = self.image_offset
        x0, y0, x1, y1 = bbox
        return (
            offset_x + int(x0 * self.display_scale),
            offset_y + int(y0 * self.display_scale),
            offset_x + int(x1 * self.display_scale),
            offset_y + int(y1 * self.display_scale),
        )

    def _make_preview(self, image: Image.Image, max_width: int, max_height: int) -> Image.Image:
        preview = image.copy()
        preview.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (max_width, max_height), "white")
        x = (max_width - preview.width) // 2
        y = (max_height - preview.height) // 2
        if preview.mode == "RGBA":
            canvas.paste(preview, (x, y), preview)
        else:
            canvas.paste(preview, (x, y))
        return canvas

    def _update_crop_scroll_region(self, _event: tk.Event | None) -> None:
        self.crop_canvas.configure(scrollregion=self.crop_canvas.bbox("all"))

    def _resize_crop_inner(self, event: tk.Event) -> None:
        self.crop_canvas.itemconfigure(self.crop_window, width=event.width)


def main() -> None:
    initial_folder = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else None
    app = CropperApp(initial_folder)
    app.mainloop()


if __name__ == "__main__":
    main()
