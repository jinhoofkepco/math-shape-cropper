from __future__ import annotations

from dataclasses import dataclass
import json
import math
import sys
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageOps, ImageTk

try:
    from .image_processing import ProcessedCrop, process_crop, sanitize_name, unique_path
except ImportError:
    from image_processing import ProcessedCrop, process_crop, sanitize_name, unique_path


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}


@dataclass
class CropRecord:
    source_path: Path
    image: Image.Image
    original_bbox: tuple[int, int, int, int]
    refined_bbox: tuple[int, int, int, int]
    suffix_var: tk.StringVar
    photo: ImageTk.PhotoImage | None = None


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
        self.current_path: Path | None = None
        self.display_scale = 1.0
        self.base_scale = 1.0
        self.zoom = 1.0
        self.image_offset = (0, 0)
        self.drag_start: tuple[int, int] | None = None
        self.selection_rect: int | None = None
        self.crop_records: list[CropRecord] = []

        self.common_name_var = tk.StringVar()
        self.save_folder_var = tk.StringVar(value="cropped_shapes")
        self.status_var = tk.StringVar(value="폴더를 선택하세요.")

        self._configure_style()
        self._build_layout()
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
            activestyle="dotbox",
            exportselection=False,
            yscrollcommand=scrollbar.set,
        )
        scrollbar.configure(command=self.file_list.yview)
        self.file_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.file_list.bind("<<ListboxSelect>>", self.on_file_selected)

    def _build_center_panel(self, parent: ttk.Frame) -> None:
        toolbar = ttk.Frame(parent, style="Toolbar.TFrame")
        toolbar.pack(fill=tk.X)

        self.page_title_var = tk.StringVar(value="선택된 페이지 없음")
        ttk.Label(toolbar, textvariable=self.page_title_var, anchor=tk.W).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=12)
        ttk.Button(toolbar, text="-", width=3, command=lambda: self.adjust_zoom(1 / 1.15)).pack(side=tk.LEFT, padx=(0, 4), pady=6)
        ttk.Button(toolbar, text="맞춤", command=self.fit_page).pack(side=tk.LEFT, padx=4, pady=6)
        ttk.Button(toolbar, text="+", width=3, command=lambda: self.adjust_zoom(1.15)).pack(side=tk.LEFT, padx=(4, 10), pady=6)

        self.page_canvas = tk.Canvas(parent, bg="#e8edf2", highlightthickness=0, cursor="crosshair")
        self.page_canvas.pack(fill=tk.BOTH, expand=True)
        self.page_canvas.bind("<Configure>", lambda _event: self.redraw_page())
        self.page_canvas.bind("<ButtonPress-1>", self.start_selection)
        self.page_canvas.bind("<B1-Motion>", self.update_selection)
        self.page_canvas.bind("<ButtonRelease-1>", self.finish_selection)

    def _build_right_panel(self, parent: ttk.Frame) -> None:
        top = ttk.Frame(parent, style="Panel.TFrame")
        top.pack(fill=tk.X, padx=10, pady=10)

        ttk.Label(top, text="공통 이름").pack(anchor=tk.W)
        ttk.Entry(top, textvariable=self.common_name_var).pack(fill=tk.X, pady=(2, 8))

        save_row = ttk.Frame(top, style="Panel.TFrame")
        save_row.pack(fill=tk.X)
        ttk.Entry(save_row, textvariable=self.save_folder_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(save_row, text="저장", command=self.save_all).pack(side=tk.LEFT, padx=(6, 0))

        base_row = ttk.Frame(top, style="Panel.TFrame")
        base_row.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(base_row, text="저장 위치", command=self.choose_output_base).pack(side=tk.LEFT)
        self.output_base_var = tk.StringVar(value="선택한 사진 폴더 기준")
        ttk.Label(base_row, textvariable=self.output_base_var, style="Muted.TLabel").pack(side=tk.LEFT, padx=(8, 0), fill=tk.X, expand=True)

        ttk.Label(parent, text="크롭한 도형", style="Muted.TLabel").pack(fill=tk.X, padx=12, pady=(2, 4))
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

    def load_folder(self, folder: Path) -> None:
        if not folder.exists():
            messagebox.showerror("폴더 없음", f"폴더를 찾을 수 없습니다.\n{folder}")
            return

        self.selected_folder = folder
        self.output_base = None
        self.output_base_var.set("선택한 사진 폴더 기준")
        self.image_paths = sorted(
            [path for path in folder.iterdir() if path.suffix.lower() in IMAGE_EXTENSIONS],
            key=lambda path: path.name.lower(),
        )
        self.file_list.delete(0, tk.END)
        for path in self.image_paths:
            self.file_list.insert(tk.END, path.name)

        self.status_var.set(f"{len(self.image_paths)}개 이미지 로드: {folder}")
        if self.image_paths:
            self.file_list.selection_set(0)
            self.file_list.activate(0)
            self.load_page(self.image_paths[0])
        else:
            self.page_title_var.set("이미지가 없습니다.")
            self.page_image = None
            self.current_path = None
            self.redraw_page()

    def on_file_selected(self, _event: tk.Event) -> None:
        selection = self.file_list.curselection()
        if not selection:
            return
        self.load_page(self.image_paths[selection[0]])

    def load_page(self, path: Path) -> None:
        try:
            image = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
        except Exception as exc:
            messagebox.showerror("이미지 열기 실패", f"{path.name}\n{exc}")
            return

        self.page_image = image
        self.current_path = path
        self.zoom = 1.0
        self.page_title_var.set(path.name)
        if not self.common_name_var.get().strip():
            self.common_name_var.set(path.stem)
        self.redraw_page()
        self.status_var.set("도형 영역을 드래그하면 오른쪽에 보정된 크롭이 추가됩니다.")

    def redraw_page(self) -> None:
        self.page_canvas.delete("all")
        self.selection_rect = None
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
        self.page_canvas.create_image(offset_x, offset_y, anchor=tk.NW, image=self.page_photo)
        self.page_canvas.create_rectangle(
            offset_x,
            offset_y,
            offset_x + display_width,
            offset_y + display_height,
            outline="#94a3b8",
        )

    def fit_page(self) -> None:
        self.zoom = 1.0
        self.redraw_page()

    def adjust_zoom(self, factor: float) -> None:
        if self.page_image is None:
            return
        self.zoom = max(0.35, min(5.0, self.zoom * factor))
        self.redraw_page()

    def start_selection(self, event: tk.Event) -> None:
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
        if self.drag_start is None or self.selection_rect is None:
            return
        end_x, end_y = self._clamp_display_point(event.x, event.y)
        self.page_canvas.coords(self.selection_rect, self.drag_start[0], self.drag_start[1], end_x, end_y)

    def finish_selection(self, event: tk.Event) -> None:
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
        try:
            processed = process_crop(self.page_image, bbox, refine_bounds=False)
        except Exception as exc:
            messagebox.showerror("크롭 실패", str(exc))
            return
        self.add_crop(processed, self.current_path)

    def add_crop(self, processed: ProcessedCrop, source_path: Path) -> None:
        suffix = f"{len(self.crop_records) + 1:03d}"
        record = CropRecord(
            source_path=source_path,
            image=processed.image,
            original_bbox=processed.original_bbox,
            refined_bbox=processed.refined_bbox,
            suffix_var=tk.StringVar(value=suffix),
        )
        self.crop_records.append(record)
        self._refresh_crop_grid(scroll_to_bottom=True)
        self.status_var.set(f"크롭 추가: {source_path.name} / {suffix}")

    def _refresh_crop_grid(self, scroll_to_bottom: bool = False) -> None:
        for child in self.crop_inner.winfo_children():
            child.destroy()
        for index, record in enumerate(self.crop_records):
            self._render_crop_card(record, row=index // 3, column=index % 3)
        self.crop_inner.update_idletasks()
        self._update_crop_scroll_region(None)
        if scroll_to_bottom:
            self.after_idle(lambda: self.crop_canvas.yview_moveto(1.0))

    def _render_crop_card(self, record: CropRecord, row: int, column: int) -> None:
        card = ttk.Frame(self.crop_inner, style="Card.TFrame")
        card.grid(row=row, column=column, sticky="nsew", padx=4, pady=4)
        card.columnconfigure(0, weight=1)

        preview = self._make_preview(record.image, max_width=94, max_height=82)
        record.photo = ImageTk.PhotoImage(preview)
        preview_label = ttk.Label(card, image=record.photo, background="#ffffff", cursor="hand2")
        preview_label.grid(row=0, column=0, sticky="ew", padx=5, pady=(5, 3))
        preview_label.bind("<Button-1>", lambda _event, item=record: self.delete_crop(item))

        ttk.Entry(card, textvariable=record.suffix_var, width=8, justify=tk.CENTER).grid(
            row=1,
            column=0,
            padx=8,
            pady=(0, 6),
        )

    def delete_crop(self, record: CropRecord) -> None:
        if record in self.crop_records:
            self.crop_records.remove(record)
        self._refresh_crop_grid()
        self.status_var.set("크롭을 삭제했습니다.")

    def save_all(self) -> None:
        if not self.crop_records:
            messagebox.showwarning("저장할 크롭 없음", "먼저 도형 영역을 크롭하세요.")
            return

        base = self.output_base or self.selected_folder or Path.cwd()
        folder_name = sanitize_name(self.save_folder_var.get(), "cropped_shapes")
        output_dir = base / folder_name
        output_dir.mkdir(parents=True, exist_ok=True)

        common = sanitize_name(self.common_name_var.get(), "shape")
        manifest: list[dict[str, object]] = []
        saved_count = 0

        for index, record in enumerate(self.crop_records, start=1):
            suffix = sanitize_name(record.suffix_var.get(), f"{index:03d}")
            file_name = f"{common}_{suffix}.png" if common else f"{suffix}.png"
            output_path = unique_path(output_dir / file_name)
            record.image.save(output_path)
            saved_count += 1
            manifest.append(
                {
                    "file": output_path.name,
                    "source": str(record.source_path),
                    "original_bbox": record.original_bbox,
                    "refined_bbox": record.refined_bbox,
                }
            )

        manifest_path = unique_path(output_dir / f"{common}_manifest.json")
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        self.status_var.set(f"{saved_count}개 저장 완료: {output_dir}")
        messagebox.showinfo("저장 완료", f"{saved_count}개 이미지를 저장했습니다.\n{output_dir}")

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

    def _make_preview(self, image: Image.Image, max_width: int, max_height: int) -> Image.Image:
        preview = image.copy()
        preview.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (max_width, max_height), "white")
        x = (max_width - preview.width) // 2
        y = (max_height - preview.height) // 2
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
