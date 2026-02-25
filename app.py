import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
import googlemaps
import polyline as polyline_decoder
from datetime import datetime
import re
import os
import firebase_admin
from firebase_admin import credentials, firestore

# 현재 파일 기준 디렉토리
APP_DIR = os.path.dirname(os.path.abspath(__file__))

st.set_page_config(page_title="🇺🇸 우리들의 미서부 여행 플래너", layout="wide")

# --- 비밀번호 인증 ---
def check_password():
    if st.session_state.get("authenticated"):
        return True

    st.title("🔒 우리들의 미국 서부 여행 플래너")
    st.markdown("접속하려면 비밀번호를 입력하세요.")

    with st.form("login_form"):
        password = st.text_input("비밀번호", type="password", placeholder="비밀번호를 입력하세요")
        submitted = st.form_submit_button("입력")

        if submitted:
            if password == st.secrets["APP_PASSWORD"]:
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("비밀번호가 틀렸습니다. 다시 시도해 주세요.")

    return False

if not check_password():
    st.stop()

# --- Firebase 초기화 ---
@st.cache_resource
def init_firebase():
    if not firebase_admin._apps:
        firebase_config = dict(st.secrets["firebase"])
        cred = credentials.Certificate(firebase_config)
        firebase_admin.initialize_app(cred)
    return firestore.client()

db = init_firebase()

# --- Firebase 저장/불러오기 함수 ---
def load_places():
    doc = db.collection("travel_data").document("places").get()
    if doc.exists:
        return doc.to_dict().get("list", [])
    return []

def save_places(places):
    db.collection("travel_data").document("places").set({"list": places})

def load_itinerary():
    doc = db.collection("travel_data").document("itinerary").get()
    if doc.exists:
        rows = doc.to_dict().get("list", [])
        if rows:
            df = pd.DataFrame(rows)
            # 이전 데이터 호환성: '시간' 컬럼이 있으면 '시작시간'으로 변환
            if '시간' in df.columns and '시작시간' not in df.columns:
                df = df.rename(columns={'시간': '시작시간'})
            for col in ['날짜', '시작시간', '종료시간', '장소 및 활동', '메모']:
                if col not in df.columns:
                    df[col] = ''
            return df[['날짜', '시작시간', '종료시간', '장소 및 활동', '메모']]
    return pd.DataFrame(columns=['날짜', '시작시간', '종료시간', '장소 및 활동', '메모'])

def save_itinerary(df):
    db.collection("travel_data").document("itinerary").set({"list": df.to_dict(orient="records")})

# --- Google Maps 초기화 ---
try:
    gmaps = googlemaps.Client(key=st.secrets["GOOGLE_MAPS_API_KEY"])
    GMAPS_API_KEY = st.secrets["GOOGLE_MAPS_API_KEY"]
except Exception:
    st.error("Google Maps API Key가 설정되지 않았습니다.")
    st.stop()

# --- 사진 URL 생성 ---
def get_photo_url(photo_reference, max_width=400):
    return (
        f"https://maps.googleapis.com/maps/api/place/photo"
        f"?maxwidth={max_width}&photo_reference={photo_reference}&key={GMAPS_API_KEY}"
    )

# --- 연속 지점 간 이동 시간 계산 ---
def get_segment_times(places):
    """각 연속 지점 쌍의 이동 시간을 계산하여 반환 (캐싱)"""
    if len(places) < 2:
        return []

    # 캐시 키: 장소 이름 목록
    cache_key = "_".join(p['name'] for p in places)
    cached = st.session_state.get('segment_times_cache', {})
    if cached.get('key') == cache_key:
        return cached.get('times', [])

    times = []
    for i in range(len(places) - 1):
        a = places[i]
        b = places[i + 1]
        try:
            dirs = gmaps.directions(
                (a['lat'], a['lng']),
                (b['lat'], b['lng']),
                mode="driving",
                language="ko"
            )
            if not dirs:
                dirs = gmaps.directions(
                    a['address'], b['address'],
                    mode="driving",
                    language="ko"
                )
            if dirs:
                leg = dirs[0]['legs'][0]
                times.append({
                    'from': a['name'],
                    'to': b['name'],
                    'duration': leg['duration']['text'],
                    'distance': leg['distance']['text'],
                    'polyline': dirs[0]['overview_polyline']['points'],
                    'mid_lat': (a['lat'] + b['lat']) / 2,
                    'mid_lng': (a['lng'] + b['lng']) / 2,
                })
            else:
                times.append(None)
        except Exception:
            times.append(None)

    st.session_state['segment_times_cache'] = {'key': cache_key, 'times': times}
    return times

# --- 초기 세션 상태 설정 (Firebase에서 불러오기) ---
if 'places' not in st.session_state:
    st.session_state['places'] = load_places()
if 'itinerary' not in st.session_state:
    st.session_state['itinerary'] = load_itinerary()
if 'search_candidates' not in st.session_state:
    st.session_state['search_candidates'] = []
if 'preview_place' not in st.session_state:
    st.session_state['preview_place'] = None
if 'route_polyline' not in st.session_state:
    st.session_state['route_polyline'] = None
if 'route_start' not in st.session_state:
    st.session_state['route_start'] = None
if 'route_end' not in st.session_state:
    st.session_state['route_end'] = None
if 'route_result' not in st.session_state:
    st.session_state['route_result'] = None
if 'segment_times_cache' not in st.session_state:
    st.session_state['segment_times_cache'] = {}
if 'show_segment_times' not in st.session_state:
    st.session_state['show_segment_times'] = False
if 'failed_place_ids' not in st.session_state:
    st.session_state['failed_place_ids'] = set()

st.title("🚙 우리들의 미국 서부 여행 플래너")

# 사이드바
with st.sidebar:
    st.image(os.path.join(APP_DIR, "ezgif.com-reverse.gif"), use_container_width=True)
    st.header("메뉴")
    if st.button("🔓 로그아웃"):
        st.session_state["authenticated"] = False
        st.rerun()

# 탭 구성
tab1, tab2 = st.tabs(["🗺️ 지도 및 경로 탐색", "📅 일정 관리"])

with tab1:
    col1, col2 = st.columns([1, 2])

    with col1:
        st.subheader("📍 관광지 검색 및 추가")
        search_query = st.text_input("관광지 이름을 영어 또는 한글로 입력하세요 (예: Grand Canyon, Las Vegas)")

        if st.button("🔍 검색") and search_query:
            try:
                autocomplete_result = gmaps.places_autocomplete(
                    search_query,
                    language="ko",
                    components={"country": "us"}
                )
            except Exception:
                autocomplete_result = []
                st.error("검색 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.")
            if autocomplete_result:
                # establishment 타입 우선 정렬 (geocode 타입은 Places Details API와 호환성 문제 발생 가능)
                establishment_results = [r for r in autocomplete_result if 'establishment' in r.get('types', [])]
                other_results = [r for r in autocomplete_result if 'establishment' not in r.get('types', [])]
                st.session_state['search_candidates'] = establishment_results + other_results
                st.session_state['preview_place'] = None
                st.session_state['failed_place_ids'] = set()
            else:
                st.session_state['search_candidates'] = []
                st.session_state['preview_place'] = None
                if autocomplete_result is not None:
                    st.error("검색 결과가 없습니다. 다른 검색어로 시도해 주세요.")

        # 후보 목록 표시 및 선택
        if st.session_state['search_candidates']:
            candidate_labels = [c['description'] for c in st.session_state['search_candidates']]
            selected_label = st.radio("검색 결과에서 장소를 선택하세요", candidate_labels)

            selected_candidate = next(
                c for c in st.session_state['search_candidates'] if c['description'] == selected_label
            )
            place_id = selected_candidate['place_id']
            current_preview = st.session_state.get('preview_place')
            failed_ids = st.session_state.get('failed_place_ids', set())

            if place_id in failed_ids:
                st.warning("⚠️ 이 장소는 상세 정보를 불러올 수 없습니다. 다른 검색 결과를 선택해 주세요.")
            elif current_preview is None or current_preview.get('place_id') != place_id:
                place_detail = None
                try:
                    place_detail = gmaps.place(
                        place_id,
                        fields=['name', 'geometry', 'formatted_address', 'rating',
                                'user_ratings_total', 'opening_hours', 'website',
                                'international_phone_number', 'photos'],
                        language="ko"
                    )
                except ValueError:
                    st.warning("⚠️ 이 장소의 상세 정보를 불러올 수 없습니다. 다른 검색 결과를 선택해 주세요.")
                    st.session_state['failed_place_ids'].add(place_id)
                    st.session_state['preview_place'] = None
                except Exception:
                    st.warning("⚠️ 네트워크 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.")
                    st.session_state['preview_place'] = None

                if place_detail is not None:
                    result = place_detail.get('result', {})
                    geometry = result.get('geometry', {}).get('location', {})
                    if result and geometry.get('lat') and geometry.get('lng'):
                        # 대표 사진 URL 추출
                        photo_url = None
                        photos = result.get('photos', [])
                        if photos:
                            photo_ref = photos[0].get('photo_reference')
                            if photo_ref:
                                photo_url = get_photo_url(photo_ref, max_width=400)

                        st.session_state['preview_place'] = {
                            'place_id': place_id,
                            'name': result.get('name', selected_label),
                            'lat': geometry['lat'],
                            'lng': geometry['lng'],
                            'address': result.get('formatted_address', ''),
                            'rating': result.get('rating'),
                            'user_ratings_total': result.get('user_ratings_total'),
                            'opening_hours': result.get('opening_hours', {}).get('weekday_text', []),
                            'website': result.get('website', ''),
                            'phone': result.get('international_phone_number', ''),
                            'photo_url': photo_url,
                        }
                    elif place_detail is not None:
                        st.warning("⚠️ 이 장소의 위치 정보를 찾을 수 없습니다. 다른 검색 결과를 선택해 주세요.")
                        st.session_state['failed_place_ids'].add(place_id)
                        st.session_state['preview_place'] = None

            # 상세 정보 표시
            preview = st.session_state['preview_place']
            if preview:
                st.divider()

                # 사진 표시
                if preview.get('photo_url'):
                    st.image(preview['photo_url'], use_container_width=True)

                st.markdown(f"### 📌 {preview['name']}")
                st.markdown(f"📍 {preview['address']}")

                if preview.get('rating'):
                    stars = "⭐" * round(preview['rating'])
                    st.markdown(f"{stars} **{preview['rating']}** ({preview.get('user_ratings_total', 0):,}개 리뷰)")

                if preview.get('phone'):
                    st.markdown(f"📞 {preview['phone']}")

                if preview.get('website'):
                    st.markdown(f"🌐 [웹사이트]({preview['website']})")

                if preview.get('opening_hours'):
                    with st.expander("🕐 영업 시간"):
                        for line in preview['opening_hours']:
                            st.markdown(f"- {line}")

                st.divider()

                existing_names = [p['name'] for p in st.session_state['places']]
                if preview['name'] in existing_names:
                    st.warning(f"'{preview['name']}'은 이미 추가된 장소입니다.")
                else:
                    if st.button("✅ 지도에 추가"):
                        new_place = {
                            'name': preview['name'],
                            'lat': preview['lat'],
                            'lng': preview['lng'],
                            'address': preview['address'],
                            'photo_url': preview.get('photo_url', ''),
                        }
                        st.session_state['places'].append(new_place)
                        save_places(st.session_state['places'])
                        # 세그먼트 캐시 초기화
                        st.session_state['segment_times_cache'] = {}
                        st.session_state['search_candidates'] = []
                        st.session_state['preview_place'] = None
                        st.success(f"'{preview['name']}' 추가 완료!")
                        st.rerun()

        # 추가된 장소 목록 및 삭제
        if st.session_state['places']:
            st.divider()
            st.subheader("📋 추가된 장소 목록")
            for i, place in enumerate(st.session_state['places']):
                col_name, col_del = st.columns([4, 1])
                with col_name:
                    st.markdown(f"**{i+1}.** {place['name']}")
                with col_del:
                    if st.button("🗑️", key=f"del_{i}"):
                        st.session_state['places'].pop(i)
                        save_places(st.session_state['places'])
                        st.session_state['segment_times_cache'] = {}
                        st.rerun()

        # 이동 시간 계산기
        st.divider()
        st.subheader("⏱️ 차량 이동 시간 계산")
        if len(st.session_state['places']) >= 2:
            place_names = [p['name'] for p in st.session_state['places']]
            start_point = st.selectbox("출발지 선택", place_names, key="start")
            end_point = st.selectbox("도착지 선택", place_names, key="end")

            if st.button("🚗 경로 계산하기"):
                if start_point != end_point:
                    start_place = next(p for p in st.session_state['places'] if p['name'] == start_point)
                    end_place = next(p for p in st.session_state['places'] if p['name'] == end_point)

                    directions = gmaps.directions(
                        (start_place['lat'], start_place['lng']),
                        (end_place['lat'], end_place['lng']),
                        mode="driving",
                        language="ko"
                    )
                    if not directions:
                        directions = gmaps.directions(
                            start_place['address'],
                            end_place['address'],
                            mode="driving",
                            language="ko"
                        )
                    if directions:
                        leg = directions[0]['legs'][0]
                        st.session_state['route_result'] = {
                            'start': start_point,
                            'end': end_point,
                            'duration': leg['duration']['text'],
                            'distance': leg['distance']['text'],
                        }
                        st.session_state['route_polyline'] = directions[0]['overview_polyline']['points']
                        st.session_state['route_start'] = start_place
                        st.session_state['route_end'] = end_place
                        st.rerun()
                    else:
                        st.error("두 지점 간의 경로를 찾을 수 없습니다.")
                else:
                    st.warning("출발지와 도착지를 다르게 설정해 주세요.")

            # 전체 경로 이동시간 표시 토글
            st.divider()
            col_seg1, col_seg2 = st.columns([3, 1])
            with col_seg1:
                st.markdown("**🗺️ 전체 구간 이동시간 지도 표시**")
            with col_seg2:
                if st.button("계산" if not st.session_state['show_segment_times'] else "숨기기"):
                    st.session_state['show_segment_times'] = not st.session_state['show_segment_times']
                    if st.session_state['show_segment_times']:
                        st.session_state['segment_times_cache'] = {}
                    st.rerun()
        else:
            st.info("이동 시간을 계산하려면 지도에 관광지를 2개 이상 추가해 주세요.")

        # 경로 결과 표시
        if st.session_state.get('route_result'):
            r = st.session_state['route_result']
            st.info(f"🚗 **{r['start']}** → **{r['end']}**\n\n⏱️ 예상 소요 시간: **{r['duration']}** | 📏 거리: **{r['distance']}**")
            if st.button("🗑️ 경로 초기화"):
                st.session_state['route_result'] = None
                st.session_state['route_polyline'] = None
                st.session_state['route_start'] = None
                st.session_state['route_end'] = None
                st.rerun()

    with col2:
        preview = st.session_state.get('preview_place')
        if preview:
            map_center = [preview['lat'], preview['lng']]
            map_zoom = 14
        elif st.session_state.get('route_start'):
            rs = st.session_state['route_start']
            re_p = st.session_state['route_end']
            map_center = [(rs['lat'] + re_p['lat']) / 2, (rs['lng'] + re_p['lng']) / 2]
            map_zoom = 6
        elif len(st.session_state['places']) > 0:
            lats = [p['lat'] for p in st.session_state['places']]
            lngs = [p['lng'] for p in st.session_state['places']]
            map_center = [sum(lats)/len(lats), sum(lngs)/len(lngs)]
            map_zoom = 6
        else:
            map_center = [36.1699, -115.1398]
            map_zoom = 6

        m = folium.Map(
            location=map_center,
            zoom_start=map_zoom,
            tiles="http://mt0.google.com/vt/lyrs=m&hl=ko&x={x}&y={y}&z={z}",
            attr="Google",
            name="Google Maps"
        )

        # 팔레트: 지점 번호별 색상
        COLORS = ["#FF6B6B", "#FF9F43", "#F7B731", "#26de81", "#45aaf2",
                  "#a55eea", "#fd9644", "#2bcbba", "#fc5c65", "#4b7bec"]

        coordinates = []

        # --- 세그먼트 이동시간 계산 (show_segment_times ON일 때) ---
        segment_times = []
        if st.session_state.get('show_segment_times') and len(st.session_state['places']) >= 2:
            with st.spinner("구간별 이동시간 계산 중..."):
                segment_times = get_segment_times(st.session_state['places'])

        # --- 세그먼트 폴리라인 & 시간 라벨 ---
        if segment_times:
            for i, seg in enumerate(segment_times):
                if seg is None:
                    continue
                a = st.session_state['places'][i]
                b = st.session_state['places'][i + 1]
                seg_color = COLORS[i % len(COLORS)]

                # 세그먼트 경로 그리기
                decoded = polyline_decoder.decode(seg['polyline'])
                full_seg = [[a['lat'], a['lng']]] + decoded + [[b['lat'], b['lng']]]
                folium.PolyLine(
                    locations=full_seg,
                    color=seg_color,
                    weight=5,
                    opacity=0.85,
                    tooltip=f"🚗 {seg['duration']} ({seg['distance']})"
                ).add_to(m)

                # 중간 지점에 이동시간 라벨 표시
                mid_lat = seg['mid_lat']
                mid_lng = seg['mid_lng']
                label_html = f"""
                <div style="
                    background: {seg_color};
                    color: white;
                    padding: 4px 8px;
                    border-radius: 12px;
                    font-size: 12px;
                    font-weight: bold;
                    font-family: 'Noto Sans KR', sans-serif;
                    white-space: nowrap;
                    box-shadow: 0 2px 6px rgba(0,0,0,0.3);
                    border: 2px solid white;
                ">🚗 {seg['duration']}</div>
                """
                folium.Marker(
                    location=[mid_lat, mid_lng],
                    icon=folium.DivIcon(
                        html=label_html,
                        icon_size=(120, 30),
                        icon_anchor=(60, 15),
                    )
                ).add_to(m)

        # --- 단순 연결선 (세그먼트 없을 때, 경로 계산 결과 있을 때 제외) ---
        for place in st.session_state['places']:
            coordinates.append([place['lat'], place['lng']])

        if not segment_times and not st.session_state.get('route_polyline'):
            if len(coordinates) >= 2:
                folium.PolyLine(
                    locations=coordinates,
                    color="#74b9ff",
                    weight=3,
                    opacity=0.6,
                    dash_array="8"
                ).add_to(m)

        # 경로 계산 결과 폴리라인 (특정 구간 경로)
        if st.session_state.get('route_polyline') and st.session_state.get('route_start') and st.session_state.get('route_end'):
            decoded = polyline_decoder.decode(st.session_state['route_polyline'])
            rs = st.session_state['route_start']
            re_place = st.session_state['route_end']
            full_route = [[rs['lat'], rs['lng']]] + decoded + [[re_place['lat'], re_place['lng']]]
            folium.PolyLine(
                locations=full_route,
                color="#0652DD",
                weight=5,
                opacity=0.9,
                tooltip="최적 경로"
            ).add_to(m)

        # --- 커스텀 마커 (사진 + 번호 배지) ---
        for i, place in enumerate(st.session_state['places']):
            color = COLORS[i % len(COLORS)]
            photo_url = place.get('photo_url', '')
            name = place['name']

            if photo_url:
                # 사진 + 번호 배지 마커
                marker_html = f"""
                <div style="
                    position: relative;
                    width: 64px;
                    text-align: center;
                    font-family: 'Noto Sans KR', sans-serif;
                ">
                    <div style="
                        width: 60px;
                        height: 60px;
                        border-radius: 50%;
                        overflow: hidden;
                        border: 3px solid {color};
                        box-shadow: 0 3px 10px rgba(0,0,0,0.4);
                        background: white;
                    ">
                        <img src="{photo_url}"
                             style="width:100%; height:100%; object-fit:cover;"
                             onerror="this.style.display='none'; this.parentElement.style.background='{color}';"
                        />
                    </div>
                    <div style="
                        position: absolute;
                        top: -6px;
                        right: -4px;
                        width: 22px;
                        height: 22px;
                        background: {color};
                        color: white;
                        border-radius: 50%;
                        font-size: 11px;
                        font-weight: bold;
                        line-height: 22px;
                        border: 2px solid white;
                        box-shadow: 0 1px 4px rgba(0,0,0,0.3);
                    ">{i+1}</div>
                    <div style="
                        margin-top: 3px;
                        background: {color};
                        color: white;
                        padding: 2px 6px;
                        border-radius: 8px;
                        font-size: 10px;
                        font-weight: bold;
                        white-space: nowrap;
                        overflow: hidden;
                        text-overflow: ellipsis;
                        max-width: 80px;
                        box-shadow: 0 1px 4px rgba(0,0,0,0.2);
                    ">{name[:10]}{'...' if len(name) > 10 else ''}</div>
                    <div style="
                        width: 0;
                        height: 0;
                        border-left: 8px solid transparent;
                        border-right: 8px solid transparent;
                        border-top: 10px solid {color};
                        margin: 0 auto;
                    "></div>
                </div>
                """
                popup_html = f"""
                <div style="font-family: 'Noto Sans KR', sans-serif; min-width: 180px;">
                    <img src="{photo_url}" style="width:100%; border-radius:8px; margin-bottom:8px;"
                         onerror="this.style.display='none';" />
                    <div style="font-weight:bold; font-size:14px; color:{color};">📍 {name}</div>
                    <div style="font-size:11px; color:#666; margin-top:4px;">{place.get('address','')}</div>
                </div>
                """
            else:
                # 사진 없을 때: 색상 원형 번호 마커
                marker_html = f"""
                <div style="
                    position: relative;
                    text-align: center;
                    font-family: 'Noto Sans KR', sans-serif;
                ">
                    <div style="
                        width: 44px;
                        height: 44px;
                        background: {color};
                        border-radius: 50%;
                        border: 3px solid white;
                        box-shadow: 0 3px 10px rgba(0,0,0,0.4);
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        color: white;
                        font-size: 18px;
                        font-weight: bold;
                        margin: 0 auto;
                    ">{i+1}</div>
                    <div style="
                        margin-top: 3px;
                        background: {color};
                        color: white;
                        padding: 2px 6px;
                        border-radius: 8px;
                        font-size: 10px;
                        font-weight: bold;
                        white-space: nowrap;
                        box-shadow: 0 1px 4px rgba(0,0,0,0.2);
                    ">{name[:10]}{'...' if len(name) > 10 else ''}</div>
                    <div style="
                        width: 0;
                        height: 0;
                        border-left: 8px solid transparent;
                        border-right: 8px solid transparent;
                        border-top: 10px solid {color};
                        margin: 0 auto;
                    "></div>
                </div>
                """
                popup_html = f"""
                <div style="font-family: 'Noto Sans KR', sans-serif; min-width: 150px;">
                    <div style="font-weight:bold; font-size:14px; color:{color};">📍 {name}</div>
                    <div style="font-size:11px; color:#666; margin-top:4px;">{place.get('address','')}</div>
                </div>
                """

            folium.Marker(
                location=[place['lat'], place['lng']],
                popup=folium.Popup(popup_html, max_width=220),
                tooltip=f"{i+1}. {name}",
                icon=folium.DivIcon(
                    html=marker_html,
                    icon_size=(90, 100),
                    icon_anchor=(45, 80),
                )
            ).add_to(m)

        # --- 미리보기 마커 (초록색 핀) ---
        if preview:
            preview_html = f"""
            <div style="
                text-align: center;
                font-family: 'Noto Sans KR', sans-serif;
            ">
                <div style="
                    background: #00b894;
                    color: white;
                    padding: 6px 10px;
                    border-radius: 10px;
                    font-size: 11px;
                    font-weight: bold;
                    box-shadow: 0 3px 8px rgba(0,0,0,0.3);
                    border: 2px solid white;
                    white-space: nowrap;
                ">📍 {preview['name'][:15]}{'...' if len(preview['name']) > 15 else ''}<br><span style="font-size:9px; opacity:0.9;">미리보기</span></div>
                <div style="
                    width: 0;
                    height: 0;
                    border-left: 8px solid transparent;
                    border-right: 8px solid transparent;
                    border-top: 10px solid #00b894;
                    margin: 0 auto;
                "></div>
            </div>
            """
            popup_html = f"""
            <div style="font-family: 'Noto Sans KR', sans-serif; min-width: 150px;">
                <div style="font-weight:bold; font-size:14px; color:#00b894;">📍 {preview['name']}</div>
                <div style="font-size:11px; color:#666; margin-top:4px;">{preview.get('address','')}</div>
            </div>
            """
            if preview.get('photo_url'):
                popup_html = f"""
                <div style="font-family: 'Noto Sans KR', sans-serif; min-width: 180px;">
                    <img src="{preview['photo_url']}" style="width:100%; border-radius:8px; margin-bottom:8px;"
                         onerror="this.style.display='none';" />
                    <div style="font-weight:bold; font-size:14px; color:#00b894;">📍 {preview['name']}</div>
                    <div style="font-size:11px; color:#666; margin-top:4px;">{preview.get('address','')}</div>
                </div>
                """
            folium.Marker(
                location=[preview['lat'], preview['lng']],
                popup=folium.Popup(popup_html, max_width=220),
                tooltip=f"📍 {preview['name']} (미리보기)",
                icon=folium.DivIcon(
                    html=preview_html,
                    icon_size=(160, 60),
                    icon_anchor=(80, 50),
                )
            ).add_to(m)

        st_folium(m, width=800, height=600, key="main_map")

        # 구간별 이동시간 요약 테이블
        if segment_times and any(s for s in segment_times):
            st.markdown("---")
            st.markdown("### 🛣️ 구간별 이동 시간")
            rows = []
            for i, seg in enumerate(segment_times):
                if seg:
                    rows.append({
                        "구간": f"{i+1} → {i+2}",
                        "출발": seg['from'][:20],
                        "도착": seg['to'][:20],
                        "소요시간": seg['duration'],
                        "거리": seg['distance'],
                    })
            if rows:
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

with tab2:
    st.header("📅 세부 일정 관리")

    with st.form("itinerary_form"):
        col_date, col_start, col_end = st.columns(3)
        with col_date:
            date = st.date_input("날짜")
        with col_start:
            start_time = st.time_input("시작 시간")
        with col_end:
            end_time = st.time_input("종료 시간")

        activity = st.text_input("장소 및 활동")
        memo = st.text_area("메모 (준비물, 예약 번호 등)")

        submitted = st.form_submit_button("일정 추가하기")

        if submitted and activity:
            new_row = pd.DataFrame({
                '날짜': [str(date)],
                '시작시간': [start_time.strftime("%H:%M")],
                '종료시간': [end_time.strftime("%H:%M")],
                '장소 및 활동': [activity],
                '메모': [memo]
            })
            st.session_state['itinerary'] = pd.concat([st.session_state['itinerary'], new_row], ignore_index=True)
            save_itinerary(st.session_state['itinerary'])
            st.success("일정이 추가되었습니다!")

    st.divider()

    if not st.session_state['itinerary'].empty:
        sorted_itinerary = st.session_state['itinerary'].sort_values(by=['날짜', '시작시간']).reset_index(drop=True)
        st.dataframe(sorted_itinerary, use_container_width=True)

        csv = sorted_itinerary.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="엑셀/CSV로 일정 다운로드",
            data=csv,
            file_name='us_west_trip_itinerary.csv',
            mime='text/csv',
        )
    else:
        st.info("아직 추가된 일정이 없습니다.")
