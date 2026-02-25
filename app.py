import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
import googlemaps
import polyline as polyline_decoder
from datetime import datetime, date as date_type
import re
import os
import base64
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

def load_flights():
    doc = db.collection("travel_data").document("flights").get()
    if doc.exists:
        return doc.to_dict().get("list", [])
    return []

def save_flights(flights):
    db.collection("travel_data").document("flights").set({"list": flights})

def load_hotels():
    doc = db.collection("travel_data").document("hotels").get()
    if doc.exists:
        return doc.to_dict().get("list", [])
    return []

def save_hotels(hotels):
    db.collection("travel_data").document("hotels").set({"list": hotels})

def load_budget():
    doc = db.collection("travel_data").document("budget").get()
    if doc.exists:
        return doc.to_dict().get("data", {})
    return {}

def save_budget(budget):
    db.collection("travel_data").document("budget").set({"data": budget})

def load_checklist():
    doc = db.collection("travel_data").document("checklist").get()
    if doc.exists:
        return doc.to_dict().get("list", [])
    return []

def save_checklist(items):
    db.collection("travel_data").document("checklist").set({"list": items})

def load_restaurants():
    doc = db.collection("travel_data").document("restaurants").get()
    if doc.exists:
        return doc.to_dict().get("list", [])
    return []

def save_restaurants(restaurants):
    db.collection("travel_data").document("restaurants").set({"list": restaurants})

def load_settings():
    doc = db.collection("travel_data").document("settings").get()
    if doc.exists:
        return doc.to_dict()
    return {}

def save_settings(settings):
    db.collection("travel_data").document("settings").set(settings)

# --- Google Maps 초기화 ---
try:
    gmaps = googlemaps.Client(key=st.secrets["GOOGLE_MAPS_API_KEY"])
    GMAPS_API_KEY = st.secrets["GOOGLE_MAPS_API_KEY"]
except Exception:
    st.error("Google Maps API Key가 설정되지 않았습니다.")
    st.stop()

# --- 애니메이션 GIF 로더 (st.image는 GIF 정지됨 → base64 HTML 필요) ---
def load_gif_html(path, width=90):
    try:
        with open(path, "rb") as f:
            data = base64.b64encode(f.read()).decode("utf-8")
        return f'<img src="data:image/gif;base64,{data}" width="{width}" style="display:block;">'
    except Exception:
        return ""

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

# --- 기본 체크리스트 항목 ---
DEFAULT_CHECKLIST = [
    {"category": "여권/서류", "name": "여권", "checked": False},
    {"category": "여권/서류", "name": "비자 확인", "checked": False},
    {"category": "여권/서류", "name": "항공권 출력/저장", "checked": False},
    {"category": "여권/서류", "name": "여행자 보험증", "checked": False},
    {"category": "여권/서류", "name": "국제운전면허증", "checked": False},
    {"category": "의류", "name": "속옷/양말 (충분히)", "checked": False},
    {"category": "의류", "name": "티셔츠", "checked": False},
    {"category": "의류", "name": "바지/반바지", "checked": False},
    {"category": "의류", "name": "자켓/스웨터", "checked": False},
    {"category": "의류", "name": "수영복", "checked": False},
    {"category": "의류", "name": "잠옷", "checked": False},
    {"category": "세면도구", "name": "칫솔/치약", "checked": False},
    {"category": "세면도구", "name": "샴푸/린스", "checked": False},
    {"category": "세면도구", "name": "선크림", "checked": False},
    {"category": "세면도구", "name": "면도기", "checked": False},
    {"category": "전자기기", "name": "스마트폰 + 충전기", "checked": False},
    {"category": "전자기기", "name": "보조배터리", "checked": False},
    {"category": "전자기기", "name": "카메라", "checked": False},
    {"category": "전자기기", "name": "이어폰", "checked": False},
    {"category": "전자기기", "name": "멀티 어댑터", "checked": False},
    {"category": "의약품", "name": "두통약", "checked": False},
    {"category": "의약품", "name": "소화제", "checked": False},
    {"category": "의약품", "name": "지사제", "checked": False},
    {"category": "의약품", "name": "밴드/일회용품", "checked": False},
    {"category": "의약품", "name": "멀미약", "checked": False},
    {"category": "기타", "name": "선글라스", "checked": False},
    {"category": "기타", "name": "모자", "checked": False},
    {"category": "기타", "name": "우산/우비", "checked": False},
    {"category": "기타", "name": "지갑/카드", "checked": False},
    {"category": "기타", "name": "현금 (USD)", "checked": False},
]

# 예산 기본 카테고리
BUDGET_CATEGORIES = ["✈️ 항공", "🏨 숙소", "🍽️ 식비", "🎢 관광/액티비티", "🛍️ 쇼핑", "🚗 교통/렌터카", "💊 기타"]

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
if 'flights' not in st.session_state:
    st.session_state['flights'] = load_flights()
if 'hotels' not in st.session_state:
    st.session_state['hotels'] = load_hotels()
if 'budget' not in st.session_state:
    st.session_state['budget'] = load_budget()
if 'checklist' not in st.session_state:
    loaded_cl = load_checklist()
    st.session_state['checklist'] = loaded_cl if loaded_cl else [dict(x) for x in DEFAULT_CHECKLIST]
if 'restaurants' not in st.session_state:
    st.session_state['restaurants'] = load_restaurants()
if 'settings' not in st.session_state:
    st.session_state['settings'] = load_settings()

st.title("🚙 우리들의 미국 서부 여행 플래너")

# 전역 CSS: 행 hover 하이라이트 & 마지막 컬럼 삭제 버튼 hover-reveal
st.markdown("""
<style>
/* 행에 패딩 추가 → 텍스트가 배경에 붙지 않게 */
div[data-testid="stHorizontalBlock"] {
    padding: 4px 10px;
    border-radius: 8px;
    align-items: center;
}
div[data-testid="stHorizontalBlock"]:hover {
    background: rgba(0,0,0,0.03);
}
/* 마지막 컬럼 삭제 버튼: 기본 숨김 */
div[data-testid="stHorizontalBlock"]
  > div[data-testid="stColumn"]:last-of-type
  button[data-testid="baseButton-secondary"] {
    opacity: 0;
    transition: opacity 0.15s ease, background-color 0.15s ease;
    background-color: transparent !important;
    border: 1px solid transparent !important;
    color: #ef4444;
    padding: 2px 8px;
    height: 30px;
    min-height: unset;
    font-size: 14px;
    line-height: 1;
}
/* hover 시 버튼 표시 */
div[data-testid="stHorizontalBlock"]:hover
  > div[data-testid="stColumn"]:last-of-type
  button[data-testid="baseButton-secondary"] {
    opacity: 1;
    background-color: #fee2e2 !important;
    border: 1px solid #fca5a5 !important;
    border-radius: 6px !important;
}
</style>
""", unsafe_allow_html=True)

# 사이드바
with st.sidebar:
    gif_html = load_gif_html(os.path.join(APP_DIR, "ezgif.com-reverse.gif"), width=90)
    if gif_html:
        st.markdown(gif_html, unsafe_allow_html=True)
    st.header("메뉴")
    if st.button("🔓 로그아웃"):
        st.session_state["authenticated"] = False
        st.rerun()

    # --- D-Day 카운트다운 ---
    st.divider()
    st.markdown("#### 📅 D-Day 카운트다운")
    _settings = st.session_state.get('settings', {})
    _dep_str = _settings.get('departure_date', '')
    try:
        _dep_default = date_type.fromisoformat(_dep_str) if _dep_str else date_type(2026, 5, 1)
    except Exception:
        _dep_default = date_type(2026, 5, 1)

    _new_dep = st.date_input("출발일 설정", value=_dep_default, key="sidebar_dep_date")
    if str(_new_dep) != _dep_str:
        st.session_state['settings']['departure_date'] = str(_new_dep)
        save_settings(st.session_state['settings'])
        st.rerun()

    _dep = _new_dep
    _today = date_type.today()
    _delta = (_dep - _today).days
    if _delta > 0:
        st.markdown(f"""
        <div style="background:linear-gradient(135deg,#667eea,#764ba2);color:white;
                    padding:16px;border-radius:12px;text-align:center;margin-top:8px;">
            <div style="font-size:13px;opacity:.9;margin-bottom:4px;">여행까지</div>
            <div style="font-size:44px;font-weight:900;line-height:1;">{_delta}</div>
            <div style="font-size:17px;font-weight:600;">일 남았어요! ✈️</div>
            <div style="font-size:11px;opacity:.8;margin-top:6px;">{_dep.strftime('%Y년 %m월 %d일')}</div>
        </div>""", unsafe_allow_html=True)
    elif _delta == 0:
        st.markdown("""
        <div style="background:linear-gradient(135deg,#f093fb,#f5576c);color:white;
                    padding:16px;border-radius:12px;text-align:center;margin-top:8px;">
            <div style="font-size:28px;font-weight:900;">D-Day! 🎉</div>
            <div style="font-size:14px;margin-top:4px;">오늘 출발이에요!</div>
        </div>""", unsafe_allow_html=True)
    else:
        st.markdown(f"""
        <div style="background:linear-gradient(135deg,#43e97b,#38f9d7);color:white;
                    padding:16px;border-radius:12px;text-align:center;margin-top:8px;">
            <div style="font-size:13px;opacity:.9;margin-bottom:4px;">여행 중! 🌴</div>
            <div style="font-size:32px;font-weight:900;">D+{abs(_delta)}</div>
            <div style="font-size:11px;opacity:.8;margin-top:4px;">출발일: {_dep.strftime('%Y년 %m월 %d일')}</div>
        </div>""", unsafe_allow_html=True)

# 탭 구성
tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
    "🗺️ 지도 및 경로",
    "📅 일정 관리",
    "✈️ 항공/교통",
    "🏨 숙소 관리",
    "💰 예산 관리",
    "📋 준비물",
    "🍽️ 맛집 리스트",
])

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

            if current_preview is None or current_preview.get('place_id') != place_id:
                place_detail = None
                fetch_error = None

                # 1차 시도: 전체 필드 요청
                try:
                    place_detail = gmaps.place(
                        place_id,
                        fields=['name', 'geometry', 'formatted_address', 'rating',
                                'user_ratings_total', 'opening_hours', 'website',
                                'international_phone_number', 'photos'],
                        language="ko"
                    )
                except ValueError:
                    # 2차 시도: 기본 필드만 (API 등급/billing 제한 대응)
                    try:
                        place_detail = gmaps.place(
                            place_id,
                            fields=['name', 'geometry', 'formatted_address'],
                            language="ko"
                        )
                    except ValueError:
                        fetch_error = "api_error"
                    except Exception:
                        fetch_error = "network_error"
                except Exception:
                    fetch_error = "network_error"

                if fetch_error == "api_error":
                    st.warning("⚠️ 이 장소의 정보를 불러올 수 없습니다. 다른 검색 결과를 선택해 주세요.")
                    st.session_state['preview_place'] = None
                elif fetch_error == "network_error":
                    st.warning("⚠️ 네트워크 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.")
                    st.session_state['preview_place'] = None
                elif place_detail is not None:
                    result = place_detail.get('result', {})
                    geometry = result.get('geometry', {}).get('location', {})
                    lat = geometry.get('lat')
                    lng = geometry.get('lng')

                    if result and lat and lng:
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
                            'lat': lat,
                            'lng': lng,
                            'address': result.get('formatted_address', ''),
                            'rating': result.get('rating'),
                            'user_ratings_total': result.get('user_ratings_total'),
                            'opening_hours': result.get('opening_hours', {}).get('weekday_text', []),
                            'website': result.get('website', ''),
                            'phone': result.get('international_phone_number', ''),
                            'photo_url': photo_url,
                        }
                    else:
                        st.warning("⚠️ 이 장소의 위치 정보를 찾을 수 없습니다. 다른 검색 결과를 선택해 주세요.")
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
            st.markdown("<hr style='margin:4px 0 6px 0; border-color:#f0f0f0;'>", unsafe_allow_html=True)
            for i, place in enumerate(st.session_state['places']):
                c_name, c_del = st.columns([9, 1])
                c_name.markdown(
                    f"<span style='color:#bbb; font-size:11px; margin-right:8px;'>{i+1}</span>"
                    f"<span style='font-size:14px;'>{place['name']}</span>",
                    unsafe_allow_html=True
                )
                with c_del:
                    if st.button("🗑️", key=f"del_{i}", use_container_width=True):
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
                if st.button("계산" if not st.session_state['show_segment_times'] else "숨기기", type="primary"):
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
            date = st.date_input("날짜", value=date_type(2026, 5, 1))
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
        elif submitted and not activity:
            st.warning("장소 및 활동을 입력해 주세요.")

    st.divider()

    if not st.session_state['itinerary'].empty:
        # 원본 인덱스 보존 정렬 (삭제 시 정확한 행 drop)
        sorted_itin = st.session_state['itinerary'].sort_values(by=['날짜', '시작시간'])

        st.subheader("📋 등록된 일정")

        # 헤더 행
        _h = st.columns([1.6, 0.75, 0.75, 3.0, 2.6, 0.6])
        for col, label in zip(_h, ["날짜", "시작", "종료", "장소 및 활동", "메모", ""]):
            col.markdown(
                f"<small style='color:#999; font-weight:600; letter-spacing:.03em;'>{label}</small>",
                unsafe_allow_html=True
            )
        st.markdown("<hr style='margin:2px 0 4px 0; border-color:#ebebeb;'>", unsafe_allow_html=True)

        # 데이터 행
        for orig_idx, row in sorted_itin.iterrows():
            c_date, c_start, c_end, c_act, c_memo, c_del = st.columns([1.6, 0.75, 0.75, 3.0, 2.6, 0.6])
            c_date.markdown(f"<span style='font-size:13px;'>{row['날짜']}</span>",  unsafe_allow_html=True)
            c_start.markdown(f"<span style='font-size:13px;'>{row['시작시간']}</span>", unsafe_allow_html=True)
            c_end.markdown(f"<span style='font-size:13px;'>{row['종료시간']}</span>", unsafe_allow_html=True)
            c_act.markdown(f"<span style='font-size:13px; font-weight:500;'>{row['장소 및 활동']}</span>", unsafe_allow_html=True)
            c_memo.markdown(f"<span style='font-size:12px; color:#777;'>{row['메모'] if row['메모'] else ''}</span>", unsafe_allow_html=True)
            with c_del:
                if st.button("🗑️", key=f"del_itin_{orig_idx}", use_container_width=True):
                    st.session_state['itinerary'] = (
                        st.session_state['itinerary'].drop(orig_idx).reset_index(drop=True)
                    )
                    save_itinerary(st.session_state['itinerary'])
                    st.rerun()

        st.divider()
        csv = sorted_itin.reset_index(drop=True).to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 엑셀/CSV로 일정 다운로드",
            data=csv,
            file_name='us_west_trip_itinerary.csv',
            mime='text/csv',
        )
    else:
        st.info("아직 추가된 일정이 없습니다.")

# ---- TAB 3: 항공/교통 정보 ----
with tab3:
    st.header("✈️ 항공 및 교통 정보")

    with st.form("flight_form"):
        st.markdown("##### 항공편 추가")
        fc1, fc2, fc3 = st.columns(3)
        with fc1:
            f_type = st.selectbox("구분", ["출발편", "귀국편", "경유편", "국내선"])
        with fc2:
            f_airline = st.text_input("항공사", placeholder="예: 대한항공")
        with fc3:
            f_no = st.text_input("편명", placeholder="예: KE011")

        fc4, fc5 = st.columns(2)
        with fc4:
            f_dep_airport = st.text_input("출발 공항", placeholder="예: 인천 (ICN)")
            f_dep_dt = st.text_input("출발 일시", placeholder="예: 2026-05-01 14:00")
        with fc5:
            f_arr_airport = st.text_input("도착 공항", placeholder="예: 로스앤젤레스 (LAX)")
            f_arr_dt = st.text_input("도착 일시", placeholder="예: 2026-05-01 09:00")

        fc6, fc7 = st.columns(2)
        with fc6:
            f_seat = st.text_input("좌석 번호", placeholder="예: 42A")
        with fc7:
            f_confirm = st.text_input("예약 확인 번호", placeholder="예: ABC123456")

        f_memo = st.text_input("메모", placeholder="예: 수하물 23kg 포함")
        f_submitted = st.form_submit_button("✈️ 항공편 추가")

        if f_submitted and f_airline and f_no:
            new_flight = {
                "type": f_type, "airline": f_airline, "flight_no": f_no,
                "dep_airport": f_dep_airport, "dep_datetime": f_dep_dt,
                "arr_airport": f_arr_airport, "arr_datetime": f_arr_dt,
                "seat": f_seat, "confirmation": f_confirm, "memo": f_memo,
            }
            st.session_state['flights'].append(new_flight)
            save_flights(st.session_state['flights'])
            st.success(f"'{f_airline} {f_no}' 항공편이 추가되었습니다!")
            st.rerun()
        elif f_submitted:
            st.warning("항공사와 편명은 필수 입력 항목입니다.")

    st.divider()

    if st.session_state['flights']:
        st.subheader("📋 등록된 항공편")
        TYPE_COLORS = {"출발편": "#667eea", "귀국편": "#f5576c", "경유편": "#f093fb", "국내선": "#43e97b"}
        for i, fl in enumerate(st.session_state['flights']):
            c_info, c_del = st.columns([11, 1])
            color = TYPE_COLORS.get(fl.get('type', '출발편'), "#667eea")
            with c_info:
                st.markdown(f"""
                <div style="border-left:4px solid {color};padding:10px 14px;
                            background:#fafafa;border-radius:0 8px 8px 0;margin:4px 0;">
                    <span style="background:{color};color:white;font-size:11px;
                                 padding:2px 8px;border-radius:10px;font-weight:600;">
                        {fl.get('type','')}</span>&nbsp;
                    <strong style="font-size:15px;">{fl.get('airline','')} {fl.get('flight_no','')}</strong>
                    {f"<span style='color:#888;font-size:12px;margin-left:8px;'>좌석 {fl.get('seat','')}</span>" if fl.get('seat') else ""}
                    <br>
                    <span style="font-size:13px;color:#444;">
                        🛫 {fl.get('dep_airport','')} {fl.get('dep_datetime','')}
                        &nbsp;→&nbsp;
                        🛬 {fl.get('arr_airport','')} {fl.get('arr_datetime','')}
                    </span>
                    {f"<br><span style='font-size:12px;color:#888;'>📌 예약번호: {fl.get('confirmation','')}</span>" if fl.get('confirmation') else ""}
                    {f"<br><span style='font-size:12px;color:#888;'>📝 {fl.get('memo','')}</span>" if fl.get('memo') else ""}
                </div>""", unsafe_allow_html=True)
            with c_del:
                if st.button("🗑️", key=f"del_flight_{i}", use_container_width=True):
                    st.session_state['flights'].pop(i)
                    save_flights(st.session_state['flights'])
                    st.rerun()
    else:
        st.info("아직 등록된 항공편이 없습니다.")

# ---- TAB 4: 숙소 관리 ----
with tab4:
    st.header("🏨 숙소 관리")

    with st.form("hotel_form"):
        st.markdown("##### 숙소 추가")
        hc1, hc2 = st.columns(2)
        with hc1:
            h_name = st.text_input("숙소 이름", placeholder="예: Marriott Downtown LA")
            h_checkin = st.date_input("체크인 날짜", value=date_type(2026, 5, 1))
            h_confirm = st.text_input("예약 확인 번호", placeholder="예: ABC123456")
        with hc2:
            h_addr = st.text_input("주소", placeholder="예: 333 S Figueroa St, Los Angeles")
            h_checkout = st.date_input("체크아웃 날짜", value=date_type(2026, 5, 3))
            h_memo = st.text_input("메모", placeholder="예: 조식 포함, 주차 가능")
        h_submitted = st.form_submit_button("🏨 숙소 추가")

        if h_submitted and h_name:
            nights = (h_checkout - h_checkin).days
            new_hotel = {
                "name": h_name, "address": h_addr,
                "checkin": str(h_checkin), "checkout": str(h_checkout),
                "nights": nights, "confirmation": h_confirm, "memo": h_memo,
            }
            st.session_state['hotels'].append(new_hotel)
            save_hotels(st.session_state['hotels'])
            st.success(f"'{h_name}' 숙소가 추가되었습니다!")
            st.rerun()
        elif h_submitted:
            st.warning("숙소 이름은 필수 입력 항목입니다.")

    st.divider()

    if st.session_state['hotels']:
        st.subheader("📋 등록된 숙소 목록")
        for i, ht in enumerate(sorted(st.session_state['hotels'], key=lambda x: x.get('checkin', ''))):
            orig_i = st.session_state['hotels'].index(ht)
            c_info, c_del = st.columns([11, 1])
            with c_info:
                nights_txt = f"{ht.get('nights', 0)}박" if ht.get('nights') else ""
                st.markdown(f"""
                <div style="border-left:4px solid #f7b731;padding:10px 14px;
                            background:#fafafa;border-radius:0 8px 8px 0;margin:4px 0;">
                    <strong style="font-size:15px;">🏨 {ht.get('name','')}</strong>
                    {f"<span style='color:#888;font-size:12px;margin-left:8px;'>{nights_txt}</span>" if nights_txt else ""}
                    <br>
                    <span style="font-size:13px;color:#444;">
                        📅 체크인: <strong>{ht.get('checkin','')}</strong>
                        &nbsp;→&nbsp;
                        체크아웃: <strong>{ht.get('checkout','')}</strong>
                    </span>
                    {f"<br><span style='font-size:12px;color:#888;'>📍 {ht.get('address','')}</span>" if ht.get('address') else ""}
                    {f"<br><span style='font-size:12px;color:#888;'>📌 예약번호: {ht.get('confirmation','')}</span>" if ht.get('confirmation') else ""}
                    {f"<br><span style='font-size:12px;color:#888;'>📝 {ht.get('memo','')}</span>" if ht.get('memo') else ""}
                </div>""", unsafe_allow_html=True)
            with c_del:
                if st.button("🗑️", key=f"del_hotel_{i}", use_container_width=True):
                    st.session_state['hotels'].pop(orig_i)
                    save_hotels(st.session_state['hotels'])
                    st.rerun()
    else:
        st.info("아직 등록된 숙소가 없습니다.")

# ---- TAB 5: 예산 관리 ----
with tab5:
    st.header("💰 예산 관리")

    budget = st.session_state['budget']
    # 초기화되지 않은 카테고리 보완
    for cat in BUDGET_CATEGORIES:
        if cat not in budget:
            budget[cat] = {"planned": 0, "actual": 0}

    st.markdown("##### 카테고리별 예산 입력 (단위: 원)")
    st.markdown("<small style='color:#888;'>예산과 실제 지출을 입력하세요. 자동으로 합계가 계산됩니다.</small>", unsafe_allow_html=True)

    with st.form("budget_form"):
        # 헤더
        bh0, bh1, bh2 = st.columns([2.5, 2, 2])
        bh0.markdown("<small style='color:#999;font-weight:600;'>카테고리</small>", unsafe_allow_html=True)
        bh1.markdown("<small style='color:#999;font-weight:600;'>예산 (원)</small>", unsafe_allow_html=True)
        bh2.markdown("<small style='color:#999;font-weight:600;'>실제 지출 (원)</small>", unsafe_allow_html=True)
        st.markdown("<hr style='margin:2px 0 6px 0;border-color:#ebebeb;'>", unsafe_allow_html=True)

        new_budget = {}
        for cat in BUDGET_CATEGORIES:
            bc0, bc1, bc2 = st.columns([2.5, 2, 2])
            bc0.markdown(f"<span style='font-size:14px;'>{cat}</span>", unsafe_allow_html=True)
            planned_val = budget[cat].get("planned", 0)
            actual_val = budget[cat].get("actual", 0)
            planned = bc1.number_input("", min_value=0, value=int(planned_val), step=10000,
                                        key=f"planned_{cat}", label_visibility="collapsed")
            actual = bc2.number_input("", min_value=0, value=int(actual_val), step=10000,
                                       key=f"actual_{cat}", label_visibility="collapsed")
            new_budget[cat] = {"planned": planned, "actual": actual}

        b_submitted = st.form_submit_button("💾 저장")
        if b_submitted:
            st.session_state['budget'] = new_budget
            save_budget(new_budget)
            st.success("예산이 저장되었습니다!")
            st.rerun()

    st.divider()

    # 요약 카드
    total_planned = sum(budget[c].get("planned", 0) for c in BUDGET_CATEGORIES)
    total_actual = sum(budget[c].get("actual", 0) for c in BUDGET_CATEGORIES)
    remaining = total_planned - total_actual

    sc1, sc2, sc3 = st.columns(3)
    sc1.markdown(f"""
    <div style="background:linear-gradient(135deg,#667eea,#764ba2);color:white;
                padding:16px;border-radius:12px;text-align:center;">
        <div style="font-size:12px;opacity:.85;margin-bottom:4px;">총 예산</div>
        <div style="font-size:22px;font-weight:800;">{total_planned:,}원</div>
    </div>""", unsafe_allow_html=True)
    sc2.markdown(f"""
    <div style="background:linear-gradient(135deg,#f093fb,#f5576c);color:white;
                padding:16px;border-radius:12px;text-align:center;">
        <div style="font-size:12px;opacity:.85;margin-bottom:4px;">총 지출</div>
        <div style="font-size:22px;font-weight:800;">{total_actual:,}원</div>
    </div>""", unsafe_allow_html=True)
    rem_color = "#43e97b,#38f9d7" if remaining >= 0 else "#fc5c65,#fd9644"
    sc3.markdown(f"""
    <div style="background:linear-gradient(135deg,{rem_color});color:white;
                padding:16px;border-radius:12px;text-align:center;">
        <div style="font-size:12px;opacity:.85;margin-bottom:4px;">{'잔액' if remaining >= 0 else '초과'}</div>
        <div style="font-size:22px;font-weight:800;">{abs(remaining):,}원</div>
    </div>""", unsafe_allow_html=True)

    # 카테고리별 지출 비율
    if total_planned > 0:
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("##### 카테고리별 진행률")
        for cat in BUDGET_CATEGORIES:
            p = budget[cat].get("planned", 0)
            a = budget[cat].get("actual", 0)
            if p > 0:
                pct = min(int(a / p * 100), 100)
                bar_color = "#ef4444" if pct >= 100 else "#f7b731" if pct >= 80 else "#43e97b"
                st.markdown(f"""
                <div style="margin-bottom:8px;">
                    <div style="display:flex;justify-content:space-between;font-size:13px;margin-bottom:3px;">
                        <span>{cat}</span>
                        <span style="color:#888;">{a:,} / {p:,}원 ({pct}%)</span>
                    </div>
                    <div style="background:#f0f0f0;border-radius:8px;height:10px;overflow:hidden;">
                        <div style="width:{pct}%;background:{bar_color};height:100%;
                                    border-radius:8px;transition:width .3s;"></div>
                    </div>
                </div>""", unsafe_allow_html=True)

# ---- TAB 6: 준비물 체크리스트 ----
with tab6:
    st.header("📋 준비물 체크리스트")

    cl_items = st.session_state['checklist']
    total_items = len(cl_items)
    checked_count = sum(1 for it in cl_items if it.get('checked', False))

    # 진행 표시
    pct_done = int(checked_count / total_items * 100) if total_items > 0 else 0
    cl_bar_color = "#43e97b" if pct_done == 100 else "#667eea"
    st.markdown(f"""
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:12px;">
        <div style="flex:1;background:#f0f0f0;border-radius:8px;height:12px;overflow:hidden;">
            <div style="width:{pct_done}%;background:{cl_bar_color};height:100%;border-radius:8px;"></div>
        </div>
        <span style="font-size:13px;color:#666;white-space:nowrap;">
            {checked_count}/{total_items} 완료 ({pct_done}%)
        </span>
    </div>""", unsafe_allow_html=True)

    # 카테고리별 표시
    categories = []
    for it in cl_items:
        cat = it.get('category', '기타')
        if cat not in categories:
            categories.append(cat)

    for cat in categories:
        cat_items = [(idx, it) for idx, it in enumerate(cl_items) if it.get('category') == cat]
        cat_checked = sum(1 for _, it in cat_items if it.get('checked', False))
        with st.expander(f"**{cat}** ({cat_checked}/{len(cat_items)})", expanded=True):
            for idx, it in cat_items:
                cl1, cl2 = st.columns([10, 1])
                checked = cl1.checkbox(
                    it.get('name', ''),
                    value=it.get('checked', False),
                    key=f"cl_{idx}"
                )
                if checked != it.get('checked', False):
                    st.session_state['checklist'][idx]['checked'] = checked
                    save_checklist(st.session_state['checklist'])
                    st.rerun()
                with cl2:
                    if st.button("🗑️", key=f"del_cl_{idx}", use_container_width=True):
                        st.session_state['checklist'].pop(idx)
                        save_checklist(st.session_state['checklist'])
                        st.rerun()

    st.divider()

    # 아이템 추가
    with st.form("checklist_add_form"):
        st.markdown("##### ➕ 항목 추가")
        add_cols = st.columns([2, 3, 1])
        with add_cols[0]:
            new_cl_cat = st.selectbox("카테고리",
                options=categories + ["직접 입력"],
                key="new_cl_cat_sel")
        with add_cols[1]:
            new_cl_name = st.text_input("항목 이름", placeholder="예: 두꺼운 패딩")
        new_cl_cat_custom = ""
        if new_cl_cat == "직접 입력":
            new_cl_cat_custom = st.text_input("새 카테고리 이름")
        cl_add_submitted = st.form_submit_button("추가")
        if cl_add_submitted and new_cl_name:
            final_cat = new_cl_cat_custom if new_cl_cat == "직접 입력" else new_cl_cat
            st.session_state['checklist'].append({"category": final_cat, "name": new_cl_name, "checked": False})
            save_checklist(st.session_state['checklist'])
            st.success(f"'{new_cl_name}' 항목이 추가되었습니다!")
            st.rerun()

    # 전체 초기화 버튼
    st.divider()
    rc1, rc2 = st.columns([4, 1])
    with rc1:
        st.markdown("<small style='color:#aaa;'>기본 체크리스트로 초기화하면 현재 목록이 삭제됩니다.</small>", unsafe_allow_html=True)
    with rc2:
        if st.button("🔄 초기화", use_container_width=True):
            st.session_state['checklist'] = [dict(x) for x in DEFAULT_CHECKLIST]
            save_checklist(st.session_state['checklist'])
            st.rerun()

# ---- TAB 7: 맛집 리스트 ----
with tab7:
    st.header("🍽️ 맛집 리스트")

    CUISINE_TYPES = ["🍔 버거/패스트푸드", "🍕 피자/이탈리안", "🌮 멕시칸", "🍱 일식/아시안",
                     "🥩 스테이크/바베큐", "🦞 씨푸드", "☕ 카페/디저트", "🍷 파인다이닝", "🍜 기타"]

    with st.form("restaurant_form"):
        st.markdown("##### 맛집 추가")
        rc1, rc2, rc3 = st.columns([3, 2, 2])
        with rc1:
            r_name = st.text_input("식당 이름", placeholder="예: In-N-Out Burger")
        with rc2:
            r_cuisine = st.selectbox("음식 종류", CUISINE_TYPES)
        with rc3:
            r_city = st.text_input("도시/위치", placeholder="예: Los Angeles")
        r_memo = st.text_input("메모", placeholder="예: 머스트 오더: 더블더블 Animal Style")
        r_submitted = st.form_submit_button("🍽️ 맛집 추가")

        if r_submitted and r_name:
            new_rest = {
                "name": r_name, "cuisine": r_cuisine,
                "city": r_city, "memo": r_memo, "visited": False,
            }
            st.session_state['restaurants'].append(new_rest)
            save_restaurants(st.session_state['restaurants'])
            st.success(f"'{r_name}' 맛집이 추가되었습니다!")
            st.rerun()
        elif r_submitted:
            st.warning("식당 이름은 필수 입력 항목입니다.")

    st.divider()

    if st.session_state['restaurants']:
        rests = st.session_state['restaurants']
        not_visited = [r for r in rests if not r.get('visited', False)]
        visited = [r for r in rests if r.get('visited', False)]
        st.markdown(f"**총 {len(rests)}곳** — 방문 완료 {len(visited)}곳 / 방문 예정 {len(not_visited)}곳")

        for section_label, section_list in [("⭕ 방문 예정", not_visited), ("✅ 방문 완료", visited)]:
            if section_list:
                st.markdown(f"###### {section_label}")
                for r in section_list:
                    orig_i = rests.index(r)
                    rc_info, rc_check, rc_del = st.columns([8, 2, 1])
                    with rc_info:
                        faded = "opacity:.5;" if r.get('visited') else ""
                        visited_badge = "<span style='background:#43e97b;color:white;font-size:10px;padding:1px 6px;border-radius:8px;margin-left:6px;'>방문완료</span>" if r.get('visited') else ""
                        st.markdown(f"""
                        <div style="{faded}padding:6px 0;">
                            <strong style="font-size:14px;">{r.get('name','')}</strong>{visited_badge}
                            <span style="font-size:12px;color:#888;margin-left:8px;">{r.get('cuisine','')}</span>
                            {f"<br><span style='font-size:12px;color:#666;'>📍 {r.get('city','')}</span>" if r.get('city') else ""}
                            {f"<br><span style='font-size:12px;color:#aaa;'>📝 {r.get('memo','')}</span>" if r.get('memo') else ""}
                        </div>""", unsafe_allow_html=True)
                    with rc_check:
                        btn_label = "↩️ 방문 취소" if r.get('visited') else "✅ 방문 완료"
                        if st.button(btn_label, key=f"visit_{orig_i}", use_container_width=True):
                            st.session_state['restaurants'][orig_i]['visited'] = not r.get('visited', False)
                            save_restaurants(st.session_state['restaurants'])
                            st.rerun()
                    with rc_del:
                        if st.button("🗑️", key=f"del_rest_{orig_i}", use_container_width=True):
                            st.session_state['restaurants'].pop(orig_i)
                            save_restaurants(st.session_state['restaurants'])
                            st.rerun()
    else:
        st.info("아직 등록된 맛집이 없습니다. 가고 싶은 맛집을 추가해 보세요! 🍜")
