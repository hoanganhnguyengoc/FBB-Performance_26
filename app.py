# -*- coding: utf-8 -*-
"""
app.py
======
Web app (Streamlit) - Portal tổng hợp, thống kê, phân tích dữ liệu
Fixed Broadband (FBB) Performance Test (Ookla raw).

Chạy:
    streamlit run app.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import pydeck as pdk
import streamlit as st

import pipeline as pl

st.set_page_config(page_title="Portal FBB Performance", page_icon="🌐", layout="wide")

st.title("🌐 Portal thống kê & phân tích Fixed Broadband Performance")
st.caption(
    "Upload file raw Speedtest Fixed Broadband (.csv / .zip / .xlsx) để lọc, "
    "tổng hợp và trực quan hóa tốc độ Download/Upload của các nhà mạng."
)

# ---------------------------------------------------------------------------
# Upload & xử lý
# ---------------------------------------------------------------------------

uploaded = st.file_uploader(
    "Chọn file raw (.csv, .zip, hoặc .xlsx)",
    type=["csv", "zip", "xlsx"],
)

if "df" not in st.session_state:
    st.session_state.df = None
    st.session_state.file_sig = None

if uploaded is not None:
    sig = (uploaded.name, uploaded.size)
    if sig != st.session_state.file_sig:
        progress = st.progress(0.0, text="Đang xử lý...")
        status = st.empty()

        steps = {"count": 0}

        def cb(msg):
            steps["count"] += 1
            status.write(msg)
            progress.progress(min(steps["count"] / 6, 1.0))

        try:
            df, counts, missing = pl.full_pipeline(uploaded, filename=uploaded.name, progress_cb=cb)
        except Exception as e:
            st.error(f"❌ Lỗi khi xử lý file: {type(e).__name__}: {e}")
            st.stop()

        progress.progress(1.0, text="Hoàn tất!")
        status.empty()

        if missing:
            st.warning(f"⚠️ File thiếu các cột sau (đã tự điền rỗng): {', '.join(missing)}")

        st.session_state.df = df
        st.session_state.counts = counts
        st.session_state.file_sig = sig

if st.session_state.df is None:
    st.info("⬆️ Hãy upload 1 file raw Fixed Broadband để bắt đầu.")
    st.stop()

df = st.session_state.df

with st.expander("📋 Số liệu minh bạch quá trình lọc dữ liệu", expanded=False):
    c = st.session_state.counts
    st.markdown(f"""
- Số dòng đọc được từ file (đã loại dòng trống hoàn toàn): **{c['input']:,}**
- Sau lọc `is_portal_included = TRUE`: **{c['after_portal_included']:,}**
- Sau lọc loại bỏ test qua VPN (`is_network_vpn`): **{c['after_vpn_filter']:,}**
- Sau lọc loại bỏ server chọn thủ công (`is_server_auto_selected`): **{c['after_manual_server_filter']:,}**
- Sau lọc khoảng cách server ≤ 10.000km & latency ≤ 3.000ms: **{c['after_distance_latency_filter']:,}**
- Sau khi tách `attr_portal_categories` thành từng dòng riêng (1 test có thể
  thuộc nhiều nhóm — All Fixed / Web / Desktop / Mobile Wifi / All Wifi /
  Mobile Broadband / Low Latency / High Latency / Ethernet / Satellite):
  **{c['after_explode_categories']:,}** dòng (1 test gốc có thể lặp lại ở
  nhiều dòng, mỗi dòng ứng với 1 nhóm).

*Lưu ý: "Spoofed", "phiên bản app tối thiểu", "Mobile WiFi first-hop latency",
"mobile-friendly download-only" là các filter chính thức của Ookla nhưng
KHÔNG áp dụng được ở đây do dữ liệu raw không có field tương ứng / không có
ngưỡng cụ thể — theo xác nhận của người dùng, coi `is_portal_included = TRUE`
là đã đủ tin cậy cho các trường hợp này.*
""")

# ---------------------------------------------------------------------------
# Sidebar: bộ lọc
# ---------------------------------------------------------------------------

st.sidebar.header("🔎 Bộ lọc")

stat_choice = st.sidebar.radio("Thống kê theo", ["median", "average"], horizontal=True,
                                format_func=lambda x: "Median" if x == "median" else "Average")

min_d, max_d = df["test_date"].min(), df["test_date"].max()
date_range = st.sidebar.date_input("Khoảng ngày (test_date)", value=(min_d, max_d),
                                    min_value=min_d, max_value=max_d)
if isinstance(date_range, tuple) and len(date_range) == 2:
    d_from, d_to = date_range
else:
    d_from = d_to = date_range

tech_options = sorted(df["technology_type"].unique())
tech_sel = st.sidebar.multiselect("Phân loại (attr_portal_categories)", tech_options,
                                   default=["All Fixed"] if "All Fixed" in tech_options else tech_options)

provider_options = sorted(df["attr_provider_name_common"].unique())
provider_sel = st.sidebar.multiselect("Nhà mạng (attr_provider_name_common)", provider_options,
                                       default=[])

platform_options = sorted(df["platform_name"].unique())
platform_sel = st.sidebar.multiselect("Nền tảng (id_platform)", platform_options, default=[])

device_search = st.sidebar.text_input("Tìm theo id_device (chính xác hoặc để trống = tất cả)")

mask = (df["test_date"] >= d_from) & (df["test_date"] <= d_to)
if tech_sel:
    mask &= df["technology_type"].isin(tech_sel)
if provider_sel:
    mask &= df["attr_provider_name_common"].isin(provider_sel)
if platform_sel:
    mask &= df["platform_name"].isin(platform_sel)
if device_search.strip():
    try:
        dev_id = int(device_search.strip())
        mask &= df["id_device"] == dev_id
    except ValueError:
        st.sidebar.warning("id_device phải là số nguyên.")

fdf = df[mask]
st.sidebar.markdown(f"**{len(fdf):,}** dòng khớp bộ lọc hiện tại.")

if fdf.empty:
    st.warning("Không có dữ liệu nào khớp bộ lọc đã chọn.")
    st.stop()

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

tab_provider, tab_device, tab_map, tab_chart = st.tabs(
    ["📊 Theo nhà mạng", "📱 Theo thiết bị (UE)", "🗺️ Bản đồ", "📈 Biểu đồ theo thời gian"]
)

# --- Tab 1: Provider summary ---
with tab_provider:
    st.subheader(f"Thống kê theo nhà mạng ({stat_choice})")
    prov_df = pl.summarize_by_provider(fdf, stat=stat_choice)
    st.dataframe(prov_df, use_container_width=True, hide_index=True)

    fig = px.bar(prov_df, x="attr_provider_name_common", y=["download_mbps", "upload_mbps"],
                 barmode="group", labels={"value": "Mbps", "attr_provider_name_common": "Nhà mạng"},
                 title=f"Download / Upload theo nhà mạng ({stat_choice})")
    st.plotly_chart(fig, use_container_width=True)

    csv_bytes = prov_df.to_csv(index=False).encode("utf-8-sig")
    st.download_button("⬇️ Tải bảng CSV", data=csv_bytes, file_name="fbb_summary_by_provider.csv", mime="text/csv")

# --- Tab 2: Device summary ---
with tab_device:
    st.subheader(f"Thống kê theo thiết bị / UE ({stat_choice})")
    dev_df = pl.summarize_by_device(fdf, stat=stat_choice)
    st.dataframe(dev_df, use_container_width=True, hide_index=True, height=500)
    st.caption(f"Tổng số thiết bị (id_device) duy nhất khớp bộ lọc: **{dev_df.shape[0]:,}**")

    csv_bytes = dev_df.to_csv(index=False).encode("utf-8-sig")
    st.download_button("⬇️ Tải bảng CSV", data=csv_bytes, file_name="fbb_summary_by_device.csv", mime="text/csv")

# --- Tab 3: Map ---
with tab_map:
    st.subheader("Bản đồ vị trí test")
    map_mode = st.radio("Chế độ hiển thị", ["Điểm (point)", "Gộp theo ô lưới (bin)"], horizontal=True)
    metric_for_map = st.selectbox("Chỉ số tô màu", ["download_mbps", "upload_mbps", "latency_ms"],
                                   format_func=lambda x: {"download_mbps": "Download (Mbps)",
                                                           "upload_mbps": "Upload (Mbps)",
                                                           "latency_ms": "Latency (ms)"}[x])

    st.markdown("**Tùy chỉnh ngưỡng & màu legend**")
    n_buckets = st.number_input("Số mức ngưỡng (bucket)", min_value=2, max_value=6, value=4)
    default_max = float(np.nanpercentile(fdf["val_download_mbps" if metric_for_map == "download_mbps"
                                              else "val_upload_mbps" if metric_for_map == "upload_mbps"
                                              else "val_latency_min_ms"], 95)) or 100.0
    cols = st.columns(int(n_buckets))
    thresholds = []
    colors = []
    default_palette = ["#d73027", "#fc8d59", "#fee08b", "#91cf60", "#1a9850", "#2c7bb6"]
    for i, c in enumerate(cols):
        with c:
            th = st.number_input(f"Ngưỡng tối đa #{i+1}", value=round(default_max * (i + 1) / n_buckets, 1),
                                  key=f"th_{i}")
            col = st.color_picker(f"Màu #{i+1}", value=default_palette[i % len(default_palette)], key=f"col_{i}")
            thresholds.append(th)
            colors.append(col)

    def hex_to_rgb(h):
        h = h.lstrip("#")
        return [int(h[j:j+2], 16) for j in (0, 2, 4)]

    def color_for_value(v):
        for th, col in zip(thresholds, colors):
            if v <= th:
                return hex_to_rgb(col)
        return hex_to_rgb(colors[-1])

    if map_mode == "Điểm (point)":
        pts = fdf.dropna(subset=["attr_location_latitude", "attr_location_longitude"]).copy()
        if len(pts) > 20000:
            st.info(f"Dữ liệu có {len(pts):,} điểm — hiển thị mẫu ngẫu nhiên 20.000 điểm để tránh quá tải trình duyệt.")
            pts = pts.sample(20000, random_state=0)
        val_col = {"download_mbps": "val_download_mbps", "upload_mbps": "val_upload_mbps",
                   "latency_ms": "val_latency_min_ms"}[metric_for_map]
        pts["color"] = pts[val_col].apply(color_for_value)
        layer = pdk.Layer(
            "ScatterplotLayer", data=pts,
            get_position=["attr_location_longitude", "attr_location_latitude"],
            get_fill_color="color", get_radius=60, pickable=True, opacity=0.6,
        )
        tooltip = {"text": f"{{attr_provider_name_common}}\n{val_col}: {{{val_col}}}"}
    else:
        zoom = st.slider("Zoom level (kích thước ô lưới theo chuẩn Ookla Map Data Bins)", 8, 18, 12, step=2)
        min_samples = st.number_input("Số mẫu tối thiểu/ô để hiển thị", min_value=1, value=2)
        bins = pl.compute_map_bins(fdf, zoom=zoom, stat=stat_choice, min_samples=min_samples)
        if bins.empty:
            st.warning("Không có ô lưới nào đủ số mẫu tối thiểu để hiển thị.")
            st.stop()
        bins["color"] = bins[metric_for_map].apply(color_for_value)
        layer = pdk.Layer(
            "ColumnLayer", data=bins,
            get_position=["bin_lon", "bin_lat"],
            get_elevation="test_count", elevation_scale=2,
            get_fill_color="color", radius=200, pickable=True,
        )
        tooltip = {"text": f"{metric_for_map}: {{{metric_for_map}}}\nsố mẫu: {{sample_count}}\nsố test: {{test_count}}"}
        pts = bins.rename(columns={"bin_lat": "attr_location_latitude", "bin_lon": "attr_location_longitude"})

    center_lat = float(pts["attr_location_latitude"].mean())
    center_lon = float(pts["attr_location_longitude"].mean())
    view_state = pdk.ViewState(latitude=center_lat, longitude=center_lon, zoom=10, pitch=30)
    st.pydeck_chart(pdk.Deck(layers=[layer], initial_view_state=view_state, tooltip=tooltip,
                              map_style="road"))

    legend_md = " &nbsp; ".join(
        f"<span style='background:{col};padding:2px 8px;border-radius:3px;color:#000'>≤ {th}</span>"
        for th, col in zip(thresholds, colors)
    )
    st.markdown(f"**Legend:** {legend_md}", unsafe_allow_html=True)

# --- Tab 4: Time series ---
with tab_chart:
    st.subheader("Biểu đồ theo thời gian")
    metric_choice = st.selectbox("Chỉ số", ["download_mbps", "upload_mbps", "latency_ms", "test_count"],
                                  format_func=lambda x: {"download_mbps": "Download (Mbps)",
                                                          "upload_mbps": "Upload (Mbps)",
                                                          "latency_ms": "Latency (ms)",
                                                          "test_count": "Số lượng test"}[x])
    group_choice = st.radio("Nhóm theo", ["Nhà mạng", "Nền tảng", "Phân loại", "Không nhóm (tổng)"], horizontal=True)
    group_map = {"Nhà mạng": "attr_provider_name_common", "Nền tảng": "platform_name",
                 "Phân loại": "technology_type", "Không nhóm (tổng)": None}
    group_col = group_map[group_choice]

    ts = pl.time_series(fdf, group_col=group_col, stat=stat_choice)

    if group_col:
        st.markdown("**Tùy chỉnh màu từng đường**")
        groups = sorted(ts[group_col].unique())
        color_map = {}
        n_show = min(len(groups), 12)
        cols = st.columns(min(n_show, 6))
        default_palette = [
            "#66c2a5", "#fc8d62", "#8da0cb", "#e78ac3", "#a6d854",
            "#ffd92f", "#e5c494", "#b3b3b3", "#1f78b4", "#33a02c",
            "#e31a1c", "#ff7f00",
        ]  # Set2 quy đổi sẵn sang hex (st.color_picker chỉ nhận hex, không nhận "rgb(...)")
        for i, g in enumerate(groups[:n_show]):
            with cols[i % len(cols)]:
                color_map[g] = st.color_picker(str(g), value=default_palette[i % len(default_palette)], key=f"line_col_{g}")
        fig = go.Figure()
        for g in groups:
            sub = ts[ts[group_col] == g]
            fig.add_trace(go.Scatter(x=sub["test_date"], y=sub[metric_choice], mode="lines+markers",
                                      name=str(g), line=dict(color=color_map.get(g))))
        fig.update_layout(title=f"{metric_choice} theo ngày, nhóm theo {group_choice} ({stat_choice})",
                           xaxis_title="Ngày", yaxis_title=metric_choice)
    else:
        fig = px.line(ts, x="test_date", y=metric_choice, markers=True,
                       title=f"{metric_choice} theo ngày (tổng, {stat_choice})")

    st.plotly_chart(fig, use_container_width=True)

    csv_bytes = ts.to_csv(index=False).encode("utf-8-sig")
    st.download_button("⬇️ Tải dữ liệu biểu đồ (CSV)", data=csv_bytes, file_name="fbb_timeseries.csv", mime="text/csv")
