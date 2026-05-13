import logging
from pathlib import Path
import pandas as pd

# --- CẤU HÌNH LOGGING ---
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "Create_sim_VIN_Modular.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILE, encoding="utf-8")]
)
logger = logging.getLogger(__name__)

# --- LỚP XỬ LÝ TOÁN HỌC & HEX ---

class AutomotiveMath:
    @staticmethod
    def calculate_raw_value(target_val, a, b, byte_size):
        try:
            raw_x = int(round((float(target_val) - float(b)) / float(a)))
            max_val = (1 << (int(byte_size) * 8)) - 1
            return max(0, min(raw_x, max_val))
        except Exception: return 0

    @staticmethod
    def to_hex_list(val, byte_size, endian='High-Low'):
        hex_val = hex(val)[2:].zfill(int(byte_size) * 2).upper()
        bytes_list = [hex_val[i:i+2] for i in range(0, len(hex_val), 2)]
        if endian == 'Low-High': bytes_list.reverse()
        return bytes_list

# --- LỚP MÃ HÓA ISO-TP ---

class ISOTPEncoder:
    def __init__(self, response_id, suffix="NONE\t0"):
        self.res_id_str = f"{response_id:08X}" if isinstance(response_id, int) else str(response_id).zfill(8).upper()
        self.suffix = suffix

    def encode(self, data_payload):
        total_len = len(data_payload)
        frames = []
        prefix = "INFO_DATABASE = Res<1"
        
        if total_len <= 7:
            frame = [f"{total_len:02X}"] + data_payload + ["00"] * (7 - total_len)
            frames.append(f"{prefix}\t\t{self.res_id_str} 08 {' '.join(frame)} \t{self.suffix}")
        else:
            ff_len = f"{total_len:03X}"
            ff = ["1" + ff_len[0], ff_len[1:]] + data_payload[:6]
            frames.append(f"{prefix}\t\t{self.res_id_str} 08 {' '.join(ff)} \t{self.suffix}")
            
            remaining = data_payload[6:]
            for i in range(0, len(remaining), 7):
                chunk = remaining[i:i+7]
                idx = (i // 7 + 1) % 16
                cf = [f"2{idx:X}"] + chunk + ["00"] * (7 - len(chunk))
                frames.append(f"INFO_DATABASE = Res<1\t\t{self.res_id_str} 08 {' '.join(cf)} \t{self.suffix}")
        return frames

# --- LỚP ĐIỀU PHỐI CHÍNH (ORCHESTRATOR) ---

class SimGenerator:
    def __init__(self, pids_df, db_df, config_params, req_id, res_id):
        """
        config_params: Dictionary chứa các cặp Key-Value cho header
        """
        self.pids = pids_df
        self.db = db_df
        self.config_params = config_params
        self.req_id_str = f"{req_id:08X}" if isinstance(req_id, int) else str(req_id).zfill(8).upper()
        self.encoder = ISOTPEncoder(response_id=res_id)

    def _build_header(self):
        """Tạo header từ dictionary parameter"""
        header = [
            "###########################################",
            "#           Auto Generated SIM            #",
            "###########################################"
        ]
        for key, value in self.config_params.items():
            header.append(f"<config sw> {key} = {value}")
        header.append("###########################################")
        return header

    def generate_content(self):
        header = self._build_header()
        note_lines = ["\n# ================= NOTES ================= #"]
        data_lines = ["\n# ================= DATA ================= #"]

        merged = self.pids.merge(self.db, on='ItemID', how='left')
        grouped = merged.groupby('GetValueCmd')

        for get_cmd, group in grouped:
            if pd.isna(get_cmd): continue
            
            total_size = int(group.iloc[0]['TotalDataSize'])
            payload_buffer = ["00"] * total_size
            
            # Request
            data_lines.append(f"INFO_DATABASE = Req>1\t\t{self.req_id_str} 08 {get_cmd} \t4\t0")

            for _, row in group.iterrows():
                note_lines.append(f"//Note: {row['ItemName']} | PID: {row['ItemID']} | Val: {row['Value']}")
                
                raw_val = AutomotiveMath.calculate_raw_value(row['Value'], row['a'], row['b'], row['Bytesize'])
                hex_bytes = AutomotiveMath.to_hex_list(raw_val, row['Bytesize'], row['Endian'])
                
                pos = int(row['BytePosition'])
                for i, b in enumerate(hex_bytes):
                    if (pos + i) < total_size: payload_buffer[pos + i] = b
            
            # Response (Combine)
            data_lines.extend(self.encoder.encode(payload_buffer))
            data_lines.append("") 

        return header + note_lines + data_lines

# --- HÀM CHUYÊN BIỆT ---

def load_data(input_path, db_ld_path, db_nws_path, db_dtc_path):
    input_ld = pd.read_excel(input_path, sheet_name='PIDs').astype(str)
    input_nws = pd.read_excel(input_path, sheet_name='NWS').astype(str)
    input_dtc = pd.read_excel(input_path, sheet_name='NWS').astype(str)
    db_ld_item = pd.read_excel(db_ld_path, sheet_name='Item ID', skiprows=[0, 2, 3]).astype(str)
    db_ld_profile = pd.read_excel(db_ld_path, sheet_name='Profile ID', skiprows=[0, 2, 3]).astype(str)
    db_nws_ymme = pd.read_excel(db_nws_path, sheet_name='Ymme', skiprows=[0, 2, 3]).astype(str)
    db_nws_profile = pd.read_excel(db_nws_path, sheet_name='Profile', skiprows=[0, 2, 3]).astype(str)
    db_nws_profile = pd.read_excel(db_dtc_path, sheet_name='DTC', skiprows=[0, 2, 3]).astype(str)
    return input_ld, db_ld_item, db_ld_profile, db_nws_ymme, db_nws_profile

def create_sim_file(pids_df, db_df, config_params, req_id, res_id, output_path):
    generator = SimGenerator(pids_df, db_df, config_params, req_id, res_id)
    content = generator.generate_content()
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(content))
    logger.info(f"File created: {output_path}")


def reformat_input():
    # Hàm này có thể được sử dụng để chuẩn hóa dữ liệu đầu vào nếu cần thiết
    pass
# --- MAIN EXECUTION ---

if __name__ == "__main__":
    # 1. Định nghĩa Config Header linh động (Dùng Dictionary)
    # Bạn có thể dễ dàng thêm/bớt các dòng config ở đây mà không cần sửa class
    Protocol_CONFIG = {
        "Protocol": "29",
        "BAUDRATE": "500000",
        "PIN_KRX_CANH": "6",
        "PIN_KTX_CANH": "14",
        "VOLT_KRX_CANH": "3",
        "VREF": "0",
        "TBYTE": "8",
        "TFRAME": "5",
        "RANGE": "0,0;"
    }



    # 3. Chạy chương trình
    try:
        ld_df, nws_df, dtc_df, REQ_ID, RES_ID = load_data('config/Vehicle_infor.xlsx', 'data/Hyundai_ABS_LD_V20.00.02_Mar172021.xlsx', 'data/Hyundai_NWScan_v20.00.04_Apr172021.xlsx', 'data/Hyundai_DTCDatabase_PCMABSSRS_V24.00.04_Nov032025.xlsx')
        create_sim_file(nws_df, ld_df, dtc_df, Protocol_CONFIG, REQ_ID, RES_ID, "demo_final.sim")
    except Exception as e:
        logger.error(f"Failed: {e}")