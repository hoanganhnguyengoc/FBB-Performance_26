# -*- coding: utf-8 -*-
"""
pipeline.py
===========
Xử lý dữ liệu raw Ookla Fixed Broadband (FBB) Performance Test cho tool
web thống kê/phân tích tương tác.

Quy trình (đã xác nhận với người dùng, xem README.md mục "Quyết định
nghiệp vụ"):

  1. Đọc raw (.csv / .zip chứa .csv / .xlsx), chỉ lấy đúng các cột cần
     thiết để giảm RAM & thời gian xử lý.
  2. Loại các dòng trống hoàn toàn (file xlsx mẫu có ~893k dòng trống ở
     cuối không chứa dữ liệu — hiện tượng "ghost rows" thường gặp khi
     export Excel).
  3. Lọc is_portal_included == True (bắt buộc theo hướng dẫn gốc).
  4. Lọc thêm (đã xác nhận với người dùng — áp dụng đúng 3 lọc có field
     rõ ràng trong dữ liệu, bỏ qua các lọc không xác định được field/
     ngưỡng cụ thể — xem README.md):
       - is_network_vpn == True  -> loại
       - is_server_auto_selected == False (chọn server thủ công) -> loại
       - attr_server_distance_km > 10000 HOẶC val_latency_min_ms > 3000
         -> loại
  5. Tách (explode) attr_portal_categories: mỗi giá trị trong danh sách
     phân tách bởi dấu phẩy trở thành 1 dòng riêng, các cột khác giữ
     nguyên (duplicate) — đúng yêu cầu gốc, giống cách làm với tool
     Mobile trước đây.
  6. Từ dữ liệu đã lọc + tách category, cung cấp các hàm tổng hợp
     (provider, device/UE, map bin, time series) với tùy chọn
     median/average.

Định danh "mẫu" (Sample Construction) dùng thống nhất id_device cho MỌI
nền tảng (kể cả Web) — đã xác nhận với người dùng dựa trên tài liệu
chính thức "Fixed Network Performance Dataset Definitions" của Ookla,
trong đó id_device được định nghĩa là "Public-facing unique ID of the
user/device" (không có field session_id/user_id riêng nào khác được
export trong bộ dữ liệu này).
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# 1. Cột cần lấy (đã xác nhận với người dùng)
# ---------------------------------------------------------------------------

# 20 cột theo đúng yêu cầu gốc trong file hướng dẫn
FIELDS_REQUESTED = [
    "ts_result_received",
    "id_result",
    "id_device",
    "attr_provider_name_common",
    "id_platform",
    "attr_location_latitude",
    "attr_location_longitude",
    "attr_place_name",
    "attr_wifi_mac_manufacturer",
    "attr_wifi_frequency_mhz",
    "attr_wifi_channel_width_mhz",
    "is_wifi_5ghz",
    "val_download_mbps",
    "val_upload_mbps",
    "val_latency_min_ms",
    "attr_server_sponsor_name",
    "is_portal_included",
    "attr_portal_categories",
    "attr_place_locality_type",
    "attr_desktop_model",
]

# 3 cột bổ sung — đã xác nhận với người dùng để áp các filter chuẩn Ookla
# có field rõ ràng (VPN / server thủ công / khoảng cách-độ trễ)
FIELDS_EXTRA_FOR_FILTERING = [
    "is_network_vpn",
    "is_server_auto_selected",
    "attr_server_distance_km",
]

FIELDS_NEEDED = FIELDS_REQUESTED + FIELDS_EXTRA_FOR_FILTERING

# Map id_platform -> tên hiển thị
PLATFORM_NAMES = {
    1: "Android",
    2: "iOS",
    3: "Web",
    4: "MacOS",
    5: "Windows",
}

# Ngưỡng filter "host distance" chính thức của Ookla (dùng chung Mobile & Fixed)
MAX_SERVER_DISTANCE_KM = 10000
MAX_LATENCY_MS = 3000

# Zoom-level bin size (độ) cho bản đồ — suy ra từ bảng "Map Data Bins" của
# Ookla (độ rộng/cao bin tính theo km tại xích đạo), quy đổi sang độ kinh/vĩ
# (1 độ vĩ ~ 111.32 km không đổi theo vĩ độ; 1 độ kinh ~ 111.32*cos(lat) km).
ZOOM_BIN_KM_AT_EQUATOR = {
    8: 156.4,
    10: 39.1,
    12: 9.77,
    14: 2.44,
    16: 0.6108,
    18: 0.1527,
}
KM_PER_DEGREE_LAT = 111.32


# ---------------------------------------------------------------------------
# 2. Đọc raw file
# ---------------------------------------------------------------------------

def _read_single_csv(buf, usecols) -> pd.DataFrame:
    return pd.read_csv(buf, usecols=lambda c: c in usecols, low_memory=False)


def _read_single_excel(buf, usecols) -> pd.DataFrame:
    try:
        return pd.read_excel(buf, usecols=lambda c: c in usecols, engine="calamine")
    except Exception:
        # fallback nếu môi trường không có python-calamine
        buf.seek(0) if hasattr(buf, "seek") else None
        return pd.read_excel(buf, usecols=lambda c: c in usecols, engine="openpyxl")


def read_raw_file(path_or_buffer, filename: Optional[str] = None,
                   usecols: Iterable[str] = FIELDS_NEEDED) -> pd.DataFrame:
    """Đọc 1 file raw FBB (.csv, .zip chứa .csv, hoặc .xlsx).

    path_or_buffer: đường dẫn str/Path, hoặc file-like object (vd từ
    Streamlit file_uploader).
    filename: tên file gốc (bắt buộc truyền khi path_or_buffer là buffer,
    để nhận diện phần mở rộng).
    """
    usecols = set(usecols)

    if isinstance(path_or_buffer, (str, Path)):
        name = str(path_or_buffer)
        buf = path_or_buffer
    else:
        name = filename or ""
        buf = path_or_buffer

    ext = Path(name).suffix.lower()

    if ext == ".zip":
        with zipfile.ZipFile(buf) as zf:
            csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            if not csv_names:
                raise ValueError(f"Không tìm thấy file .csv nào trong {name}")
            frames = []
            for n in csv_names:
                with zf.open(n) as f:
                    frames.append(_read_single_csv(f, usecols))
            df = pd.concat(frames, ignore_index=True)
    elif ext == ".csv":
        df = _read_single_csv(buf, usecols)
    elif ext in (".xlsx", ".xls"):
        df = _read_single_excel(buf, usecols)
    else:
        raise ValueError(f"Định dạng file không hỗ trợ: {name} (chỉ hỗ trợ .csv/.zip/.xlsx)")

    # Đảm bảo đủ cột (field thiếu -> tạo cột rỗng để code phía sau không lỗi,
    # đồng thời trả về danh sách cột bị thiếu để cảnh báo người dùng)
    missing = [c for c in usecols if c not in df.columns]
    for c in missing:
        df[c] = np.nan

    return df, missing


# ---------------------------------------------------------------------------
# 3. Dọn dữ liệu: loại dòng trống, lọc, tách category
# ---------------------------------------------------------------------------

def drop_blank_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Loại các dòng hoàn toàn trống (ghost rows do Excel export thừa dòng).

    Dùng id_result (khóa test, luôn phải có giá trị với 1 test thật) làm
    tiêu chí: dòng nào id_result rỗng/NaN thì loại.
    """
    before = len(df)
    df = df[df["id_result"].notna()].copy()
    after = len(df)
    return df, before - after


def apply_quality_filters(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Áp các filter đã xác nhận với người dùng, theo đúng thứ tự:

      1. is_portal_included == True (bắt buộc, theo hướng dẫn gốc)
      2. is_network_vpn != True
      3. is_server_auto_selected != False (loại server chọn thủ công)
      4. attr_server_distance_km <= 10000 AND val_latency_min_ms <= 3000

    Trả về (df đã lọc, dict đếm số dòng bị loại ở từng bước — để hiển thị
    minh bạch trên UI, không "âm thầm" loại dữ liệu).
    """
    counts = {"input": len(df)}

    df = df[df["is_portal_included"] == True]  # noqa: E712
    counts["after_portal_included"] = len(df)

    df = df[~(df["is_network_vpn"] == True)]  # noqa: E712  (giữ cả NaN)
    counts["after_vpn_filter"] = len(df)

    df = df[~(df["is_server_auto_selected"] == False)]  # noqa: E712 (giữ cả NaN)
    counts["after_manual_server_filter"] = len(df)

    dist_ok = df["attr_server_distance_km"].isna() | (df["attr_server_distance_km"] <= MAX_SERVER_DISTANCE_KM)
    lat_ok = df["val_latency_min_ms"].isna() | (df["val_latency_min_ms"] <= MAX_LATENCY_MS)
    df = df[dist_ok & lat_ok]
    counts["after_distance_latency_filter"] = len(df)

    return df, counts


def explode_portal_categories(df: pd.DataFrame) -> pd.DataFrame:
    """Tách attr_portal_categories (danh sách phân tách bởi dấu phẩy)
    thành 1 dòng riêng cho mỗi giá trị, các cột khác duplicate nguyên vẹn.

    Lưu ý quan trọng (đã kiểm chứng trên dữ liệu thật, không phải giả
    định): các giá trị trong attr_portal_categories KHÔNG loại trừ lẫn
    nhau — vd 1 test có thể vừa là "All Fixed", vừa "Web", vừa "Low
    Latency" cùng lúc. Sau khi explode, 1 test gốc sẽ xuất hiện ở NHIỀU
    dòng (1 dòng / category) — đây là chủ đích, để khi người dùng lọc
    theo 1 category cụ thể (vd "Mobile Broadband") thì test_count/
    sample_count tính đúng cho breakout đó, giống cách Ookla portal hiển
    thị (All Fixed / Web / Desktop / Mobile Wifi / All Wifi / Mobile
    Broadband / Low Latency / High Latency đều là các "lát cắt" riêng,
    không phải các nhóm con loại trừ nhau).
    """
    s = df["attr_portal_categories"].fillna("").astype(str)
    split = s.str.split(",")
    out = df.loc[df.index.repeat(split.str.len().clip(lower=1))].copy()
    flat = [v.strip() for sub in split for v in (sub if sub else [""])]
    out["technology_type"] = flat
    out = out[out["technology_type"] != ""]
    return out.reset_index(drop=True)


def full_pipeline(path_or_buffer, filename: Optional[str] = None,
                   progress_cb=None) -> tuple[pd.DataFrame, dict, list]:
    """Chạy toàn bộ bước 1->5, trả về (df_final, report_counts, missing_cols)."""

    def _p(msg):
        if progress_cb:
            progress_cb(msg)

    _p("Đang đọc file raw...")
    df, missing = read_raw_file(path_or_buffer, filename=filename)

    _p("Đang loại bỏ dòng trống...")
    df, n_blank = drop_blank_rows(df)

    _p("Đang áp các filter chất lượng...")
    df, filter_counts = apply_quality_filters(df)
    filter_counts["blank_rows_removed"] = n_blank

    _p("Đang tách attr_portal_categories...")
    df = explode_portal_categories(df)
    filter_counts["after_explode_categories"] = len(df)

    _p("Đang chuẩn hóa kiểu dữ liệu...")
    df["ts_result_received"] = pd.to_datetime(df["ts_result_received"], errors="coerce")
    df["test_date"] = df["ts_result_received"].dt.date
    df["platform_name"] = df["id_platform"].map(PLATFORM_NAMES).fillna("Khác")
    df["attr_provider_name_common"] = df["attr_provider_name_common"].fillna("(Không xác định)")
    try:
        df["id_device"] = df["id_device"].astype("Int64")
    except (TypeError, ValueError):
        pass

    _p("Hoàn tất.")
    return df, filter_counts, missing


# ---------------------------------------------------------------------------
# 4. Tổng hợp / thống kê
# ---------------------------------------------------------------------------

def _agg_func(stat: str):
    if stat == "median":
        return "median"
    if stat == "average":
        return "mean"
    raise ValueError("stat phải là 'median' hoặc 'average'")


def summarize_by_provider(df: pd.DataFrame, stat: str = "median") -> pd.DataFrame:
    """Thống kê theo nhà mạng: test_count, device_count, DL/UL/latency,
    khoảng cách TB tới server — tính trên dữ liệu ĐÃ Sample Construction
    (trung bình theo id_device trước, rồi mới median/average theo nhóm,
    để 1 thiết bị test nhiều lần không lấn át kết quả — đúng tinh thần
    "Sample Construction" của Ookla)."""
    f = _agg_func(stat)

    sample = (
        df.groupby(["attr_provider_name_common", "id_device"], as_index=False)
        .agg(
            val_download_mbps=("val_download_mbps", "mean"),
            val_upload_mbps=("val_upload_mbps", "mean"),
            val_latency_min_ms=("val_latency_min_ms", "mean"),
            attr_server_distance_km=("attr_server_distance_km", "mean") if "attr_server_distance_km" in df.columns else ("val_download_mbps", "size"),
        )
    )

    test_counts = df.groupby("attr_provider_name_common").size().rename("test_count")

    out = sample.groupby("attr_provider_name_common").agg(
        device_count=("id_device", "nunique"),
        download_mbps=("val_download_mbps", f),
        upload_mbps=("val_upload_mbps", f),
        latency_ms=("val_latency_min_ms", f),
        avg_distance_km=("attr_server_distance_km", "mean"),
    ).join(test_counts).reset_index()

    out = out.sort_values("test_count", ascending=False).reset_index(drop=True)
    return out


def summarize_by_device(df: pd.DataFrame, stat: str = "median") -> pd.DataFrame:
    """Thống kê theo id_device (UE/wifi device)."""
    f = _agg_func(stat)
    out = df.groupby("id_device").agg(
        attr_provider_name_common=("attr_provider_name_common", lambda s: s.mode().iat[0] if not s.mode().empty else s.iloc[0]),
        platform_name=("platform_name", lambda s: s.mode().iat[0] if not s.mode().empty else s.iloc[0]),
        attr_wifi_mac_manufacturer=("attr_wifi_mac_manufacturer", "first"),
        attr_desktop_model=("attr_desktop_model", "first"),
        test_count=("id_device", "size"),
        download_mbps=("val_download_mbps", f),
        upload_mbps=("val_upload_mbps", f),
        latency_ms=("val_latency_min_ms", f),
        first_test=("ts_result_received", "min"),
        last_test=("ts_result_received", "max"),
    ).reset_index()
    return out.sort_values("test_count", ascending=False).reset_index(drop=True)


def time_series(df: pd.DataFrame, group_col: Optional[str] = "attr_provider_name_common",
                 stat: str = "median") -> pd.DataFrame:
    """Chuỗi thời gian theo ngày (test_date), group theo group_col (vd
    nhà mạng) hoặc None để gộp tất cả. Trả về long-format để vẽ Plotly
    line chart dễ dàng."""
    f = _agg_func(stat)
    cols = ["test_date"] + ([group_col] if group_col else [])
    g = df.groupby(cols).agg(
        download_mbps=("val_download_mbps", f),
        upload_mbps=("val_upload_mbps", f),
        latency_ms=("val_latency_min_ms", f),
        test_count=("id_device", "size"),
    ).reset_index()
    return g.sort_values("test_date")


def _bin_size_degrees(zoom: int, lat_deg: float) -> tuple[float, float]:
    """Trả về (bin_height_deg, bin_width_deg) cho 1 zoom level, hiệu
    chỉnh theo vĩ độ (kinh độ co lại theo cos(lat))."""
    km = ZOOM_BIN_KM_AT_EQUATOR.get(zoom)
    if km is None:
        # nội suy log-tuyến tính giữa 2 mốc gần nhất
        zs = sorted(ZOOM_BIN_KM_AT_EQUATOR)
        z_lo = max([z for z in zs if z <= zoom], default=zs[0])
        z_hi = min([z for z in zs if z >= zoom], default=zs[-1])
        if z_lo == z_hi:
            km = ZOOM_BIN_KM_AT_EQUATOR[z_lo]
        else:
            km_lo, km_hi = ZOOM_BIN_KM_AT_EQUATOR[z_lo], ZOOM_BIN_KM_AT_EQUATOR[z_hi]
            frac = (zoom - z_lo) / (z_hi - z_lo)
            km = km_lo * (km_hi / km_lo) ** frac  # nội suy theo tỉ lệ (mỗi zoom ~x4 x2)
    bin_h_deg = km / KM_PER_DEGREE_LAT
    bin_w_deg = km / (KM_PER_DEGREE_LAT * max(np.cos(np.radians(lat_deg)), 0.01))
    return bin_h_deg, bin_w_deg


def compute_map_bins(df: pd.DataFrame, zoom: int = 14, stat: str = "median",
                      min_samples: int = 2) -> pd.DataFrame:
    """Tổng hợp dữ liệu theo bin lưới địa lý ở 1 zoom level, theo đúng
    phương pháp "Map Data Bins" của Ookla (kích thước bin theo bảng tra
    cứu zoom -> km, quy đổi sang độ kinh/vĩ tùy vĩ độ trung bình của dữ
    liệu). Bin nào có số sample (id_device duy nhất) < min_samples sẽ bị
    loại khỏi kết quả hiển thị (đúng ngưỡng tối thiểu Ookla áp dụng để
    tránh lộ thông tin cá nhân khi mẫu quá nhỏ)."""
    f = _agg_func(stat)
    d = df.dropna(subset=["attr_location_latitude", "attr_location_longitude"]).copy()
    if d.empty:
        return d

    mean_lat = d["attr_location_latitude"].mean()
    bin_h, bin_w = _bin_size_degrees(zoom, mean_lat)

    d["bin_lat"] = (d["attr_location_latitude"] / bin_h).round().astype(int) * bin_h
    d["bin_lon"] = (d["attr_location_longitude"] / bin_w).round().astype(int) * bin_w

    out = d.groupby(["bin_lat", "bin_lon"]).agg(
        download_mbps=("val_download_mbps", f),
        upload_mbps=("val_upload_mbps", f),
        latency_ms=("val_latency_min_ms", f),
        test_count=("id_device", "size"),
        sample_count=("id_device", "nunique"),
    ).reset_index()

    out = out[out["sample_count"] >= min_samples]
    return out
