import os
import re
import subprocess
import polars as pl
import customtkinter as ctk
from tkinter import filedialog, messagebox, ttk

# Thiết kế giao diện theo phong cách hiện đại
ctk.set_appearance_mode("System")  # Khớp với chế độ sáng/tối của hệ điều hành
ctk.set_default_color_theme("blue")

class SimAnalyzerApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Diagnostic .SIM File Analyzer")
        self.geometry("1100 => 600")
        
        self.folder_path = ""
        self.selected_file_name = ""

        # --- Bố cục UI ---
        # 1. Khung chức năng phía trên (Top Frame)
        self.top_frame = ctk.CTkFrame(self)
        self.top_frame.pack(fill="x", padx=15, pady=10)

        self.btn_open = ctk.CTkButton(self.top_frame, text="📁 Chọn Thư Mục", command=self.browse_folder, font=("Segoe UI", 13, "bold"))
        self.btn_open.pack(side="left", padx=10, pady=10)

        self.lbl_folder = ctk.CTkLabel(self.top_frame, text="Chưa chọn thư mục nào...", text_color="gray", font=("Segoe UI", 12))
        self.lbl_folder.pack(side="left", padx=10, pady=10, fill="x", expand=True, anchor="w")

        # 2. Khung hiển thị bảng dữ liệu (Center Frame)
        self.center_frame = ctk.CTkFrame(self)
        self.center_frame.pack(fill="both", expand=True, padx=15, pady=5)

        # Định nghĩa bảng hiển thị bằng Treeview của ttk (được custom style)
        self.setup_table_style()
        
        columns = ("filename", "protocol", "baudrate", "init_cmd", "dtc_cmd", "total_req")
        self.tree = ttk.Treeview(self.center_frame, columns=columns, show="headings")
        
        # Đặt tiêu đề cho các cột
        self.tree.heading("filename", text="Tên File")
        self.tree.heading("protocol", text="Giao Thức")
        self.tree.heading("baudrate", text="Baudrate")
        self.tree.heading("init_cmd", text="Lệnh Khởi Tạo (Init)")
        self.tree.heading("dtc_cmd", text="Lệnh Đọc DTC")
        self.tree.heading("total_req", text="Tổng Số Lệnh")

        # Cấu hình độ rộng cột
        self.tree.column("filename", width=250, anchor="w")
        self.tree.column("protocol", width=150, anchor="center")
        self.tree.column("baudrate", width=100, anchor="center")
        self.tree.column("init_cmd", width=250, anchor="w")
        self.tree.column("dtc_cmd", width=250, anchor="w")
        self.tree.column("total_req", width=90, anchor="center")

        # Thanh cuộn cho bảng
        scrollbar = ttk.Scrollbar(self.center_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        
        self.tree.pack(side="left", fill="both", expand=True, padx=(5,0), pady=5)
        scrollbar.pack(side="right", fill="y", padx=(0,5), pady=5)

        # Ràng buộc sự kiện Double-click để mở file nhanh
        self.tree.bind("<Double-1>", self.open_file_directly)

        # 3. Thanh trạng thái phía dưới (Status Bar)
        self.status_label = ctk.CTkLabel(self, text="💡 Hướng dẫn: Click đúp vào một hàng bất kỳ để mở file trực tiếp bằng Notepad.", text_color="gray", font=("Segoe UI", 11, "italic"))
        self.status_label.pack(side="bottom", fill="x", padx=15, pady=5, anchor="w")

    def setup_table_style(self):
        style = ttk.Style()
        style.theme_use("default")
        style.configure("Treeview",
                        background="#2a2d2e",
                        foreground="white",
                        rowheight=28,
                        fieldbackground="#2a2d2e",
                        font=("Segoe UI", 11))
        style.map("Treeview", background=[('selected', '#1f538d')])
        style.configure("Treeview.Heading",
                        background="#1f538d",
                        foreground="white",
                        font=("Segoe UI", 11, "bold"))

    def analyze_sim_file(self, file_path):
        """Phân tích nội dung bên trong file .sim"""
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()

        baudrate = "Unknown"
        protocol_raw = "Unknown"
        init_commands = []
        dtc_commands = []
        req_count = 0

        # Từ điển phân loại dịch vụ nhanh
        for line in lines:
            line = line.strip()
            
            # Đọc cấu hình phần cứng
            if "BAUDRATE" in line:
                match = re.search(r"BAUDRATE\s*=\s*(\d+)", line)
                if match: baudrate = match.group(1)
            if "Protocol" in line or "PROTOCOL" in line:
                match = re.search(r"Protocol\s*=\s*(\d+)", line, re.IGNORECASE)
                if match: protocol_raw = match.group(1)

            # Phân tích dòng dữ liệu Request
            if "INFO_DATABASE =" in line and "Req>" in line and "//" not in line:
                req_count += 1
                parts = re.split(r'\s+', line)
                hex_bytes = [p.upper() for p in parts if re.match(r'^[0-9A-Fa-f]{2}$', p)]
                
                if hex_bytes:
                    # Xác định SID dựa vào cấu trúc gói tin (KWP hoặc CAN/UDS)
                    sid = ""
                    if protocol_raw == "15" or baudrate == "10400": # KWP
                        sid = hex_bytes[0] if hex_bytes[0] in ["81", "82"] else (hex_bytes[3] if len(hex_bytes) >= 4 else "")
                    else: # Mặc định xử lý kiểu CAN/UDS
                        sid = hex_bytes[1] if len(hex_bytes) >= 2 and hex_bytes[0] in ["01", "02", "03", "04", "05"] else hex_bytes[0]

                    # Nhóm các dòng lệnh quan trọng
                    cmd_str = " ".join(hex_bytes)
                    if sid in ["81", "10"]:
                        init_commands.append(f"{sid} ({cmd_str})")
                    elif sid in ["13", "18", "19"]:
                        dtc_commands.append(f"{sid} ({cmd_str})")

        # Xác định giao thức tổng quan
        if protocol_raw == "15" or baudrate == "10400":
            detected_proto = "K-Line / KWP2000"
        elif "can" in str(lines).lower() or baudrate in ["250000", "500000"]:
            detected_proto = "CAN bus"
        else:
            detected_proto = f"Protocol {protocol_raw}" if protocol_raw != "Unknown" else "Diagnostic Raw"

        return {
            "filename": os.path.basename(file_path),
            "protocol": detected_proto,
            "baudrate": baudrate,
            "init_cmd": ", ".join(dict.fromkeys(init_commands)) if init_commands else "Không có",
            "dtc_cmd": ", ".join(dict.fromkeys(dtc_commands)) if dtc_commands else "Không có",
            "total_req": req_count
        }

    def browse_folder(self):
        """Sự kiện click nút chọn thư mục"""
        selected_dir = filedialog.askdirectory()
        if not selected_dir:
            return

        self.folder_path = selected_dir
        self.lbl_folder.configure(text=self.folder_path, text_color="white")
        
        # Xóa bảng cũ trước khi nạp dữ liệu mới
        for item in self.tree.get_children():
            self.tree.delete(item)

        # Quét và lọc file theo đúng quy tắc
        parsed_data = []
        for filename in os.listdir(self.folder_path):
            # Điều kiện nghiêm ngặt: Kết thúc bằng .sim VÀ không phải là file correct.sim
            if filename.endswith(".sim") and filename.lower() != "correct.sim":
                file_path = os.path.join(self.folder_path, filename)
                try:
                    analysis = self.analyze_sim_file(file_path)
                    parsed_data.append(analysis)
                except Exception as e:
                    print(f"Lỗi khi xử lý file {filename}: {e}")

        # Đẩy dữ liệu lên bảng thông qua Polars để đảm bảo tốc độ mượt mà
        if parsed_data:
            df = pl.DataFrame(parsed_data)
            for row in df.iter_rows(named=True):
                self.tree.insert("", "end", values=(
                    row["filename"],
                    row["protocol"],
                    row["baudrate"],
                    row["init_cmd"],
                    row["dtc_cmd"],
                    row["total_req"]
                ))
            self.status_label.configure(text=f"✅ Đã tìm thấy và phân tích thành công {len(parsed_data)} file thỏa mãn điều kiện .sim", text_color="#4CAF50")
        else:
            self.status_label.configure(text="⚠️ Không tìm thấy file .sim hợp lệ nào (hoặc chỉ có file correct.sim/file khác đuôi nên đã bị bỏ qua).", text_color="#FFCC00")

    def open_file_directly(self, event):
        """Sự kiện click đúp để mở file trực tiếp bằng Notepad hoặc chương trình mặc định"""
        selected_item = self.tree.selection()
        if not selected_item:
            return

        item_values = self.tree.item(selected_item[0], "values")
        filename = item_values[0]
        full_file_path = os.path.normpath(os.path.join(self.folder_path, filename))

        if os.path.exists(full_file_path):
            try:
                # Tự động nhận diện hệ điều hành để mở file bằng ứng dụng mặc định (Windows dùng os.startfile)
                if hasattr(os, "startfile"):
                    os.startfile(full_file_path)
                else:
                    # Dành cho MacOS / Linux nếu có dùng tới
                    subprocess.call(["open" if os.uname().sysname == "Darwin" else "xdg-open", full_file_path])
            except Exception as e:
                messagebox.showerror("Lỗi mở file", f"Không thể mở file {filename}.\nChi tiết: {e}")
        else:
            messagebox.showerror("Lỗi đường dẫn", "File không tồn tại hoặc đã bị di chuyển!")

if __name__ == "__main__":
    app = SimAnalyzerApp()
    app.mainloop()