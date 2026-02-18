import json
from pathlib import Path
import math
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="Stroke Clinical Helper", page_icon="🧠", layout="wide")


# =========================================================
# 클립보드 복사 버튼(UI)
# =========================================================
def copy_to_clipboard_ui(text: str, button_label: str, key: str):
    safe = json.dumps(text)
    html = f"""
    <div style="display:flex; gap:10px; align-items:center; margin:6px 0;">
      <button
        style="padding:8px 12px; border-radius:10px; border:1px solid #bbb; background:#fff; cursor:pointer;"
        onclick="navigator.clipboard.writeText({safe}).then(()=>{{document.getElementById('{key}').innerText='복사되었습니다.';}});"
      >
        {button_label}
      </button>
      <span id="{key}" style="font-size:0.9rem; color:#2e7d32;"></span>
    </div>
    """
    components.html(html, height=60)


# =========================================================
# 공통 유틸/계산
# =========================================================
def cockcroft_gault_crcl(age, weight_kg, scr_mg_dl, female: bool):
    if scr_mg_dl <= 0:
        return None
    crcl = ((140 - age) * weight_kg) / (72 * scr_mg_dl)
    if female:
        crcl *= 0.85
    return crcl


def chads_vasc_score(chf, htn, age, dm, stroke_tia, vascular, female):
    score = 0
    score += 1 if chf else 0
    score += 1 if htn else 0
    score += 2 if age >= 75 else (1 if age >= 65 else 0)
    score += 1 if dm else 0
    score += 2 if stroke_tia else 0
    score += 1 if vascular else 0
    score += 1 if female else 0
    return score


def abcd2_score(age_ge_60, bp_ge_140_90, unilateral_weakness, speech_without_weakness, duration_min, diabetes):
    score = 0
    score += 1 if age_ge_60 else 0
    score += 1 if bp_ge_140_90 else 0
    if unilateral_weakness:
        score += 2
    elif speech_without_weakness:
        score += 1
    if duration_min >= 60:
        score += 2
    elif 10 <= duration_min <= 59:
        score += 1
    score += 1 if diabetes else 0
    return score


def has_bled_score(htn_sbp_gt160, renal, liver, stroke, bleed, inr_labile, age_gt65, drugs, alcohol):
    score = 0
    score += 1 if htn_sbp_gt160 else 0
    score += 1 if renal else 0
    score += 1 if liver else 0
    score += 1 if stroke else 0
    score += 1 if bleed else 0
    score += 1 if inr_labile else 0
    score += 1 if age_gt65 else 0
    score += 1 if drugs else 0
    score += 1 if alcohol else 0
    return score


# =========================================================
# NOAC 용량(단순 규칙 기반 표시)
# =========================================================
def noac_dose_apixaban(age, weight_kg, scr_mg_dl):
    criteria = 0
    criteria += 1 if age >= 80 else 0
    criteria += 1 if weight_kg <= 60 else 0
    criteria += 1 if scr_mg_dl >= 1.5 else 0
    if criteria >= 2:
        return "2.5 mg BID", "감량 기준(나이/체중/Cr 중 2개 이상) 충족입니다."
    return "5 mg BID", "표준 용량입니다."


def noac_dose_rivaroxaban(crcl):
    if crcl is None:
        return "-", "CrCl 계산이 필요합니다."
    if crcl > 50:
        return "20 mg QD (with food)", "표준 용량입니다."
    if 15 <= crcl <= 50:
        return "15 mg QD (with food)", "감량(CrCl 15–50)입니다."
    return "검토 필요", "비권고 또는 전문 검토가 필요합니다."


def noac_dose_edoxaban(crcl, weight_kg):
    if crcl is None:
        return "-", "CrCl 계산이 필요합니다."
    if crcl < 15:
        return "검토 필요", "비권고 또는 전문 검토가 필요합니다."
    if (15 <= crcl <= 50) or (weight_kg <= 60):
        return "30 mg QD", "감량(CrCl 15–50 또는 체중≤60)입니다."
    if crcl > 95:
        return "라벨 확인 필요", "AF 적응증에서 CrCl>95 제한이 있을 수 있어 확인이 필요합니다."
    return "60 mg QD", "표준 용량입니다."


def noac_dose_dabigatran(crcl, age):
    if crcl is None:
        return "-", "CrCl 계산이 필요합니다."
    if crcl < 15:
        return "검토 필요", "비권고 또는 전문 검토가 필요합니다."
    if 15 <= crcl <= 30:
        return "라벨에 따라 상이", "국가/라벨에 따라 권장 용량이 달라질 수 있습니다."
    if age >= 80:
        return "감량 고려", "고령에서는 감량 옵션을 고려하되 라벨 확인이 필요합니다."
    return "150 mg BID", "표준 용량입니다."


# =========================================================
# NIHSS (숫자 입력 + 친절한 항목명)
# =========================================================
NIHSS_ITEMS = [
    ("1a. Level of consciousness (LOC)", 0, 3),
    ("1b. LOC questions", 0, 2),
    ("1c. LOC commands", 0, 2),
    ("2. Best gaze", 0, 2),
    ("3. Visual fields", 0, 3),
    ("4. Facial palsy", 0, 3),
    ("5a. Motor arm (Left)", 0, 4),
    ("5b. Motor arm (Right)", 0, 4),
    ("6a. Motor leg (Left)", 0, 4),
    ("6b. Motor leg (Right)", 0, 4),
    ("7. Limb ataxia", 0, 2),
    ("8. Sensory", 0, 2),
    ("9. Best language", 0, 3),
    ("10. Dysarthria", 0, 2),
    ("11. Extinction and inattention (Neglect)", 0, 2),
]


def motor_MRC_from_nihss(val: int) -> str:
    mapping = {0: "V", 1: "IV", 2: "III", 3: "II", 4: "I"}
    return mapping.get(val, "N/A")


def mse_from_nihss_1a(val: int) -> str:
    mapping = {0: "alert", 1: "mild drowsy", 2: "drowsy", 3: "semicoma"}
    return mapping.get(val, "unknown")


def language_from_nihss_9(val: int) -> str:
    mapping = {
        0: "normal",
        1: "mild aphasia (language score 1)",
        2: "moderate aphasia (language score 2)",
        3: "severe aphasia (language score 3)",
    }
    return mapping.get(val, "unknown")


def build_nihss_component_text(nihss_vals: dict) -> str:
    total = sum(nihss_vals.values())
    lines = ["NIHSS components:"]
    for name, *_ in NIHSS_ITEMS:
        lines.append(f"- {name}: {nihss_vals[name]}")
    lines.append(f"NIHSS total: {total}")
    return "\n".join(lines)


def build_neuro_exam_text(nihss_vals: dict, facial_side: str, sensory_side: str, ataxia_side: str) -> str:
    loc = nihss_vals["1a. Level of consciousness (LOC)"]
    gaze = nihss_vals["2. Best gaze"]
    lang = nihss_vals["9. Best language"]
    dys = nihss_vals["10. Dysarthria"]
    neglect = nihss_vals["11. Extinction and inattention (Neglect)"]
    sensory = nihss_vals["8. Sensory"]
    ataxia = nihss_vals["7. Limb ataxia"]

    arm_l = nihss_vals["5a. Motor arm (Left)"]
    arm_r = nihss_vals["5b. Motor arm (Right)"]
    leg_l = nihss_vals["6a. Motor leg (Left)"]
    leg_r = nihss_vals["6b. Motor leg (Right)"]

    total = sum(nihss_vals.values())

    lines = []
    lines.append("Neurologic examination:")

    lines.append(f"MSE: {mse_from_nihss_1a(loc)}")
    lines.append(f"Language function: {language_from_nihss_9(lang)}")

    if gaze == 0:
        lines.append("EOM: normal")
    else:
        lines.append("EOM: gaze preponderance (+)")

    lines.append(f"dysarthria {'(+)' if dys > 0 else '(-)'}")

    lines.append("Motor")
    lines.append(f"V/V")
    lines.append(f"V/V")
    lines.append(f"(Motor grade는 NIHSS motor 점수에 따라 자동으로 표기됩니다.)")
    lines.append(f"LUE/RUE: {motor_MRC_from_nihss(arm_l)}/{motor_MRC_from_nihss(arm_r)}")
    lines.append(f"LLE/RLE: {motor_MRC_from_nihss(leg_l)}/{motor_MRC_from_nihss(leg_r)}")

    if sensory > 0:
        side = sensory_side.lower()
        lines.append(f"Sensory: {side} hypesthesia (+)")
    else:
        lines.append("Sensory: (-)")

    if ataxia > 0:
        if ataxia_side == "Left":
            lines.append("Cerebellar function test: left dysmetria (+)")
        elif ataxia_side == "Right":
            lines.append("Cerebellar function test: right dysmetria (+)")
        else:
            lines.append("Cerebellar function test: bilateral dysmetria (+)")
    else:
        lines.append("Cerebellar function test: (-)")

    lines.append(f"neglect {'(+)' if neglect > 0 else '(-)'}")

    facial_val = nihss_vals["4. Facial palsy"]
    if facial_val > 0:
        if facial_side == "Left":
            lines.append("Facial expression: left CTFP")
        elif facial_side == "Right":
            lines.append("Facial expression: right CTFP")
        else:
            lines.append("Facial expression: bilateral facial palsy (+)")
    else:
        lines.append("Facial expression: (-)")

    lines.append(f"NIHSS total: {total}")
    return "\n".join(lines)


# =========================================================
# ELAN (병변 1–4개, 크기 >1.5cm 체크박스)
# - PCA cortical branch는 후순환계로 처리합니다.
# =========================================================
SEVERITY_ORDER = {"Minor": 1, "Moderate": 2, "Major": 3}


def elan_severity_for_lesion(
    circ: str,
    size_gt_1_5: bool,
    anterior_pattern: str,
    posterior_site: str,
    anterior_multiterritory: bool,
    anterior_major_pattern: str,
):
    # 후순환계
    if circ == "후순환계":
        # Major: brainstem/cerebellum > 1.5cm
        if posterior_site in ["뇌간", "소뇌"] and size_gt_1_5:
            return "Major"

        # Moderate site examples (후순환계에서 PCA cortical branch를 지원)
        if posterior_site in ["후대뇌동맥 피질 표재 가지"]:
            return "Moderate"

        # 그 외는 크기 기준으로 단순 분류
        return "Minor" if not size_gt_1_5 else "Moderate"

    # 전순환계 Major 우선
    if anterior_major_pattern == "전체 영역 침범":
        return "Major"
    if anterior_major_pattern == "피질 표재 가지 2개 이상":
        return "Major"
    if anterior_major_pattern == "피질 표재 가지 + 심부 가지 동반":
        return "Major"
    if anterior_multiterritory:
        return "Major"

    # Moderate 패턴 (전순환계)
    if anterior_pattern in [
        "중대뇌동맥 피질 표재 가지",
        "중대뇌동맥 심부 가지",
        "경계영역(internal borderzone)",
        "전대뇌동맥 피질 표재 가지",
    ]:
        return "Moderate"

    # 그 외는 크기 기준
    return "Minor" if not size_gt_1_5 else "Moderate"


def elan_overall_severity(lesions: list[str]) -> str:
    base = max(lesions, key=lambda x: SEVERITY_ORDER[x])
    minor_count = sum(1 for x in lesions if x == "Minor")
    mod_count = sum(1 for x in lesions if x == "Moderate")
    if base == "Minor" and minor_count >= 2:
        return "Moderate"
    if base in ["Minor", "Moderate"] and mod_count >= 2:
        return "Major"
    return base


def elan_recommendation(severity: str) -> str:
    if severity in ["Minor", "Moderate"]:
        return "≤ 48시간"
    return "6–7일"


# =========================================================
# MAGIC (단계형)
# =========================================================
def reset_magic():
    st.session_state.magic_step = 0
    st.session_state.magic_answers = {}


def magic_result_from_answers(a: dict) -> str:
    if a.get("other_determined"):
        return "Other determined"

    if a.get("lacunar"):
        if a.get("relevant_artery"):
            if a.get("branch_atheroma"):
                return "LAA-BR"
            return "LAA-LC"
        if a.get("ce_source"):
            return "CE (high risk)" if a.get("ce_high_risk") else "UD negative"
        return "SVO"

    if a.get("relevant_artery"):
        return "LAA-NG" if a.get("non_generic_pattern") else "LAA"

    if a.get("ce_source"):
        return "CE (high risk)" if a.get("ce_high_risk") else "UD negative"

    return "UD negative"


# =========================================================
# ASCVD / Dyslipidemia (AHA PCE + ESC SCORE2)
# =========================================================
def aha_very_high_risk(major_events_count: int, high_risk_conditions_count: int) -> bool:
    if major_events_count >= 2:
        return True
    if major_events_count == 1 and high_risk_conditions_count >= 2:
        return True
    return False


# AHA high-risk conditions: 체크박스로 변경
AHA_HR_CONDITIONS_CHECK = [
    "나이 ≥65세",
    "당뇨병",
    "고혈압",
    "만성신질환(CKD)",
    "현재 흡연",
    "심부전",
    "이전 PCI/CABG",
    "지속적으로 LDL-C 상승(치료에도)",
]

# ESC 정의(근거 탭에서 테이블로 상세 노출)
ESC_DOC_ASCVDS = [
    "이전 ACS(심근경색 또는 불안정 협심증)",
    "만성 관상동맥증후군(chronic coronary syndromes)",
    "관상동맥/말초혈관 재개통술(PCI, CABG 등)",
    "뇌졸중 또는 TIA",
    "말초동맥질환(PAD)",
    "영상에서 확실한 ASCVD(관상동맥 CT/조영술 유의미 플라크, 경동맥/대퇴동맥 플라크, CAC 현저히 상승 등)",
]


# ---------- AHA 10-year ASCVD risk (PCE) ----------
# 2013 ACC/AHA PCE 계수 기반 (White/AA 남/여) 계산
# 주의: 이는 교육/의사결정 보조용이며, 공식 도구와 차이가 있을 수 있습니다.
PCE_COEFFS = {
    ("Male", "White"): {
        "ln_age": 12.344,
        "ln_tc": 11.853,
        "ln_age_ln_tc": -2.664,
        "ln_hdl": -7.990,
        "ln_age_ln_hdl": 1.769,
        "ln_sbp_treated": 1.797,
        "ln_sbp_untreated": 1.764,
        "smoker": 7.837,
        "ln_age_smoker": -1.795,
        "diabetes": 0.658,
        "mean": 61.18,
        "baseline_survival": 0.9144,
    },
    ("Female", "White"): {
        "ln_age": -29.799,
        "ln_age_sq": 4.884,
        "ln_tc": 13.540,
        "ln_age_ln_tc": -3.114,
        "ln_hdl": -13.578,
        "ln_age_ln_hdl": 3.149,
        "ln_sbp_treated": 2.019,
        "ln_sbp_untreated": 1.957,
        "smoker": 7.574,
        "ln_age_smoker": -1.665,
        "diabetes": 0.661,
        "mean": -29.18,
        "baseline_survival": 0.9665,
    },
    ("Male", "African American"): {
        "ln_age": 2.469,
        "ln_age_sq": 0.0,
        "ln_tc": 0.302,
        "ln_age_ln_tc": 0.0,
        "ln_hdl": -0.307,
        "ln_age_ln_hdl": 0.0,
        "ln_sbp_treated": 1.916,
        "ln_sbp_untreated": 1.809,
        "smoker": 0.549,
        "ln_age_smoker": 0.0,
        "diabetes": 0.645,
        "mean": 19.54,
        "baseline_survival": 0.8954,
    },
    ("Female", "African American"): {
        "ln_age": 17.114,
        "ln_age_sq": 0.0,
        "ln_tc": 0.940,
        "ln_age_ln_tc": 0.0,
        "ln_hdl": -18.920,
        "ln_age_ln_hdl": 4.475,
        "ln_sbp_treated": 29.291,
        "ln_sbp_untreated": 27.820,
        "smoker": 0.691,
        "ln_age_smoker": 0.0,
        "diabetes": 0.874,
        "mean": 86.61,
        "baseline_survival": 0.9533,
    },
}


def pce_10y_risk_percent(
    sex: str,
    race: str,
    age: float,
    tc: float,
    hdl: float,
    sbp: float,
    bp_treated: bool,
    smoker: bool,
    diabetes: bool,
):
    # input guards
    if age <= 0 or tc <= 0 or hdl <= 0 or sbp <= 0:
        return None

    key = (sex, race)
    if key not in PCE_COEFFS:
        return None
    c = PCE_COEFFS[key]

    ln_age = math.log(age)
    ln_tc = math.log(tc)
    ln_hdl = math.log(hdl)
    ln_sbp = math.log(sbp)

    s = 0.0
    s += c.get("ln_age", 0) * ln_age
    if "ln_age_sq" in c and c["ln_age_sq"] != 0:
        s += c["ln_age_sq"] * (ln_age ** 2)

    s += c.get("ln_tc", 0) * ln_tc
    s += c.get("ln_age_ln_tc", 0) * ln_age * ln_tc

    s += c.get("ln_hdl", 0) * ln_hdl
    s += c.get("ln_age_ln_hdl", 0) * ln_age * ln_hdl

    if bp_treated:
        s += c.get("ln_sbp_treated", 0) * ln_sbp
    else:
        s += c.get("ln_sbp_untreated", 0) * ln_sbp

    s += c.get("smoker", 0) * (1 if smoker else 0)
    s += c.get("ln_age_smoker", 0) * ln_age * (1 if smoker else 0)
    s += c.get("diabetes", 0) * (1 if diabetes else 0)

    # risk = 1 - S0 ^ exp(s - mean)
    exp_term = math.exp(s - c["mean"])
    risk = 1 - (c["baseline_survival"] ** exp_term)
    return max(0.0, min(1.0, risk)) * 100.0


# ---------- ESC SCORE2 (계산 구조 제공 + 추정치) ----------
# 실제 SCORE2는 국가 리스크 클러스터/연령대/계수/차트가 필요합니다.
# 이번 구현은 입력값을 기반으로 "추정치"를 계산하여 컷오프(2/10/20%)와 함께 표시합니다.
def score2_estimate_percent(age, sex, smoker, sbp, non_hdl, risk_region):
    # 매우 단순한 추정 모델(설명용). 공식 계산기와 다를 수 있습니다.
    base = 0.0
    base += (age - 40) * 0.18
    base += 6.0 if smoker else 0.0
    base += (sbp - 120) * 0.05
    base += (non_hdl - 130) * 0.03
    if sex == "남성":
        base *= 1.20
    # risk region multiplier
    mult = {"Low": 0.9, "Moderate": 1.0, "High": 1.15, "Very high": 1.3}.get(risk_region, 1.0)
    base *= mult

    # map to %
    # base가 0~100 사이로 지나치게 튀지 않도록 sigmoid
    p = 100.0 / (1.0 + math.exp(-0.07 * (base - 25)))
    return float(max(0.1, min(50.0, p)))


def esc_risk_category_from_score2(score2_percent: float):
    # ESC 2025 Table 3 cutoffs: <2 low, 2-<10 moderate, 10-<20 high, >=20 very high
    if score2_percent >= 20:
        return "Very high"
    if score2_percent >= 10:
        return "High"
    if score2_percent >= 2:
        return "Moderate"
    return "Low"


def esc_ldl_target_by_category(category: str) -> str:
    if category == "Very high (recurrent within 2y)":
        return "<40 mg/dL (및 ≥50% 감소를 목표로 하시는 것이 일반적입니다.)"
    if category == "Very high":
        return "<55 mg/dL (및 ≥50% 감소를 목표로 하시는 것이 일반적입니다.)"
    if category == "High":
        return "<70 mg/dL (및 ≥50% 감소를 함께 고려하실 수 있습니다.)"
    if category == "Moderate":
        return "<100 mg/dL를 목표로 하실 수 있습니다."
    if category == "Low":
        return "<116 mg/dL를 목표로 하실 수 있습니다."
    return "위험도 분류가 필요합니다."


# =========================================================
# 참고용 위험도 표 (ABCD2 / CHA2DS2-VASc)
# =========================================================
ABCD2_RISK_TABLE = pd.DataFrame(
    [
        {"ABCD²": "0–3 (Low)", "2-day risk": "1.0%", "7-day risk": "1.2%", "90-day risk": "3.1%"},
        {"ABCD²": "4–5 (Moderate)", "2-day risk": "4.1%", "7-day risk": "5.9%", "90-day risk": "9.8%"},
        {"ABCD²": "6–7 (High)", "2-day risk": "8.1%", "7-day risk": "11.7%", "90-day risk": "17.8%"},
    ]
)

CHA2DS2_VASC_RISK_TABLE = pd.DataFrame(
    [
        {"Score": 0, "Annual stroke/systemic embolism risk": "0.2%"},
        {"Score": 1, "Annual stroke/systemic embolism risk": "0.6%"},
        {"Score": 2, "Annual stroke/systemic embolism risk": "2.2%"},
        {"Score": 3, "Annual stroke/systemic embolism risk": "3.2%"},
        {"Score": 4, "Annual stroke/systemic embolism risk": "4.8%"},
        {"Score": 5, "Annual stroke/systemic embolism risk": "7.2%"},
        {"Score": 6, "Annual stroke/systemic embolism risk": "9.7%"},
        {"Score": 7, "Annual stroke/systemic embolism risk": "11.2%"},
        {"Score": 8, "Annual stroke/systemic embolism risk": "10.8%"},
        {"Score": 9, "Annual stroke/systemic embolism risk": "12.2%"},
    ]
)


# =========================================================
# 앱 시작 UI
# =========================================================
st.title("🧠 Stroke Helper")

with st.expander("면책 안내", expanded=True):
    st.write(
        "본 애플리케이션은 교육 및 임상 의사결정 보조 목적입니다. "
        "실제 치료 결정은 최신 가이드라인, 의약품 라벨, 기관 프로토콜, 환자 개별 상황을 종합하여 판단하셔야 합니다."
    )

if "is_clinician" not in st.session_state:
    st.session_state.is_clinician = None

if st.session_state.is_clinician is None:
    st.subheader("의료인 여부 확인")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("의료인입니다. 계속 진행합니다."):
            st.session_state.is_clinician = True
            st.rerun()
    with c2:
        if st.button("의료인이 아닙니다. 종료합니다."):
            st.session_state.is_clinician = False
            st.rerun()
    st.stop()

if st.session_state.is_clinician is False:
    st.error("의료인 전용 기능으로 구성되어 있어 사용을 종료합니다.")
    st.stop()

tab_calc, tab_ref = st.tabs(["🧾 임상정보 입력", "📚 가이드라인 및 근거"])


# =========================================================
# 1) 임상정보 입력
# =========================================================
with tab_calc:
    t1, t2, t3, t4 = st.tabs(["🧮 점수/계산", "⏱️ ELAN timing", "🧭 MAGIC mechanism", "🫀 Dyslipidemia (ASCVD/LDL)"])

    # ---------------------------
    # 점수/계산
    # ---------------------------
    with t1:
        score_tabs = st.tabs([
            "NIHSS",
            "CHA₂DS₂-VASc",
            "ABCD²",
            "HAS-BLED",
            "NOAC 용량(단일 약제)",
            "NOAC 용량(전체 비교)",
        ])

        # NIHSS
        with score_tabs[0]:
            st.subheader("NIHSS")
            st.write("항목별 점수를 숫자로 입력하시면 총점과 의무기록용 텍스트를 생성합니다.")

            nihss_vals = {}
            for name, mn, mx in NIHSS_ITEMS:
                nihss_vals[name] = st.number_input(name, mn, mx, 0, 1, key=f"nihss_{name}")

            total = sum(nihss_vals.values())
            st.success(f"NIHSS 총점은 {total}점입니다.")

            facial_side = "Left"
            if nihss_vals["4. Facial palsy"] > 0:
                facial_side = st.radio("Facial palsy 방향을 선택해 주십시오.", ["Left", "Right", "Bilateral"], horizontal=True)

            sensory_side = "Left"
            if nihss_vals["8. Sensory"] > 0:
                sensory_side = st.radio("감각저하 방향을 선택해 주십시오.", ["Left", "Right"], horizontal=True)

            ataxia_side = "Left"
            if nihss_vals["7. Limb ataxia"] > 0:
                ataxia_side = st.radio("Ataxia 방향을 선택해 주십시오.", ["Left", "Right", "Bilateral"], horizontal=True)

            st.divider()

            comp_text = build_nihss_component_text(nihss_vals)
            st.markdown("#### 의무기록용 NIHSS 구성요소")
            st.code(comp_text, language="text")
            copy_to_clipboard_ui(comp_text, "복사(NIHSS 구성요소)", "copy_nihss_components")

            neuro_text = build_neuro_exam_text(nihss_vals, facial_side, sensory_side, ataxia_side)
            st.markdown("#### 의무기록용 Neurologic examination")
            st.code(neuro_text, language="text")
            copy_to_clipboard_ui(neuro_text, "복사(Neurologic examination)", "copy_neuro_exam")

        # CHADS-VASc
        with score_tabs[1]:
            st.subheader("CHA₂DS₂-VASc")
            st.write("입력된 점수에 따라 연간 뇌졸중/전신색전증 위험도를 참고로 표시합니다.")
            c1, c2, c3 = st.columns(3)
            with c1:
                chf = st.checkbox("Congestive HF/LV dysfunction")
                htn = st.checkbox("Hypertension")
                dm = st.checkbox("Diabetes mellitus")
            with c2:
                age = st.number_input("Age", 0, 120, 70, 1)
                stroke_tia = st.checkbox("Prior stroke/TIA/thromboembolism")
                vascular = st.checkbox("Vascular disease (MI/PAD/aortic plaque)")
            with c3:
                sex = st.selectbox("Sex", ["Male", "Female"])
                female = (sex == "Female")

            score = chads_vasc_score(chf, htn, age, dm, stroke_tia, vascular, female)
            st.success(f"CHA₂DS₂-VASc 점수는 {score}점입니다.")

            row = CHA2DS2_VASC_RISK_TABLE[CHA2DS2_VASC_RISK_TABLE["Score"] == score]
            if not row.empty:
                st.info(f"참고 연간 위험도는 {row.iloc[0]['Annual stroke/systemic embolism risk']}입니다.")

        # ABCD2
        with score_tabs[2]:
            st.subheader("ABCD²")
            st.write("TIA 이후 단기 뇌졸중 재발 위험(2일/7일/90일)을 참고로 표시합니다.")
            c1, c2, c3 = st.columns(3)
            with c1:
                age_ge_60 = st.checkbox("Age ≥60")
                diabetes = st.checkbox("Diabetes")
            with c2:
                bp_ge = st.checkbox("BP ≥140/90 at presentation")
                duration = st.number_input("Symptom duration (minutes)", 0, 10000, 20, 5)
            with c3:
                unilateral = st.checkbox("Unilateral weakness")
                speech = st.checkbox("Speech impairment without weakness")

            score = abcd2_score(age_ge_60, bp_ge, unilateral, speech, duration, diabetes)
            st.success(f"ABCD² 점수는 {score}점입니다.")

            if score <= 3:
                rr = ABCD2_RISK_TABLE.iloc[0]
                st.info("위험군은 Low(0–3)입니다.")
            elif score <= 5:
                rr = ABCD2_RISK_TABLE.iloc[1]
                st.warning("위험군은 Moderate(4–5)입니다.")
            else:
                rr = ABCD2_RISK_TABLE.iloc[2]
                st.error("위험군은 High(6–7)입니다.")

            st.info(f"참고 위험도는 2일 {rr['2-day risk']}, 7일 {rr['7-day risk']}, 90일 {rr['90-day risk']}입니다.")

        # HAS-BLED
        with score_tabs[3]:
            st.subheader("HAS-BLED")
            st.write("항응고 치료 중 출혈 위험 요인을 점검하기 위한 점수입니다.")
            c1, c2, c3 = st.columns(3)
            with c1:
                htn160 = st.checkbox("Hypertension (SBP >160)")
                renal = st.checkbox("Abnormal renal function")
                liver = st.checkbox("Abnormal liver function")
            with c2:
                stroke = st.checkbox("Stroke history")
                bleed = st.checkbox("Bleeding history/predisposition")
                inr = st.checkbox("Labile INR (if on warfarin)")
            with c3:
                age65 = st.checkbox("Age >65")
                drugs = st.checkbox("Drugs predisposing to bleeding (antiplatelet/NSAID)")
                alcohol = st.checkbox("Alcohol use (excess)")

            score = has_bled_score(htn160, renal, liver, stroke, bleed, inr, age65, drugs, alcohol)
            st.success(f"HAS-BLED 점수는 {score}점입니다.")

        # NOAC 단일
        with score_tabs[4]:
            st.subheader("NOAC 용량(단일 약제)")
            st.write("입력값으로 CrCl을 계산하고 선택한 NOAC의 용량(표준/감량)을 표시합니다.")
            drug = st.selectbox("NOAC 선택", ["Apixaban", "Rivaroxaban", "Edoxaban", "Dabigatran"])
            age = st.number_input("Age (years)", 0, 120, 75, 1, key="noac_age")
            sex = st.selectbox("Sex", ["Male", "Female"], key="noac_sex")
            weight = st.number_input("Weight (kg)", 1.0, 300.0, 70.0, 0.5, key="noac_wt")
            scr = st.number_input("Serum creatinine (mg/dL)", 0.1, 20.0, 1.0, 0.1, key="noac_scr")
            female = (sex == "Female")
            crcl = cockcroft_gault_crcl(age, weight, scr, female)

            if crcl is not None:
                st.info(f"Cockcroft–Gault CrCl은 약 {crcl:.1f} mL/min입니다.")
            else:
                st.warning("CrCl 계산이 불가능합니다.")

            if drug == "Apixaban":
                dose, tag = noac_dose_apixaban(age, weight, scr)
            elif drug == "Rivaroxaban":
                dose, tag = noac_dose_rivaroxaban(crcl)
            elif drug == "Edoxaban":
                dose, tag = noac_dose_edoxaban(crcl, weight)
            else:
                dose, tag = noac_dose_dabigatran(crcl, age)

            st.success(f"{drug} 권장 용량 표시는 '{dose}'이며, 판단 근거는 '{tag}'입니다.")

        # NOAC 전체 비교
        with score_tabs[5]:
            st.subheader("NOAC 용량(전체 비교)")
            st.write("동일 입력값에서 4가지 NOAC의 표준/감량 판단을 한 번에 비교합니다.")
            age = st.number_input("Age (years)", 0, 120, 75, 1, key="noac_all_age")
            sex = st.selectbox("Sex", ["Male", "Female"], key="noac_all_sex")
            weight = st.number_input("Weight (kg)", 1.0, 300.0, 70.0, 0.5, key="noac_all_wt")
            scr = st.number_input("Serum creatinine (mg/dL)", 0.1, 20.0, 1.0, 0.1, key="noac_all_scr")
            female = (sex == "Female")
            crcl = cockcroft_gault_crcl(age, weight, scr, female)

            if crcl is not None:
                st.info(f"Cockcroft–Gault CrCl은 약 {crcl:.1f} mL/min입니다.")
            else:
                st.warning("CrCl 계산이 불가능합니다.")

            apx_d, apx_tag = noac_dose_apixaban(age, weight, scr)
            riva_d, riva_tag = noac_dose_rivaroxaban(crcl)
            edox_d, edox_tag = noac_dose_edoxaban(crcl, weight)
            dabi_d, dabi_tag = noac_dose_dabigatran(crcl, age)

            df = pd.DataFrame([
                {"NOAC": "Apixaban", "Dose": apx_d, "Decision": apx_tag, "Key rule (summary)": "감량: age≥80, wt≤60, SCr≥1.5 중 2개 이상"},
                {"NOAC": "Rivaroxaban", "Dose": riva_d, "Decision": riva_tag, "Key rule (summary)": "CrCl>50: 20mg, CrCl 15–50: 15mg"},
                {"NOAC": "Edoxaban", "Dose": edox_d, "Decision": edox_tag, "Key rule (summary)": "감량: CrCl 15–50 또는 wt≤60"},
                {"NOAC": "Dabigatran", "Dose": dabi_d, "Decision": dabi_tag, "Key rule (summary)": "CrCl 15–30 및 고령은 라벨 확인 필요"},
            ])
            st.dataframe(df, use_container_width=True)

            note = "\n".join([
                "NOAC dose comparison (educational):",
                f"- Age={age}, Sex={sex}, Weight={weight} kg, SCr={scr} mg/dL, CrCl≈{crcl:.1f} mL/min" if crcl is not None else "- CrCl 계산 불가",
                f"- Apixaban: {apx_d} ({apx_tag})",
                f"- Rivaroxaban: {riva_d} ({riva_tag})",
                f"- Edoxaban: {edox_d} ({edox_tag})",
                f"- Dabigatran: {dabi_d} ({dabi_tag})",
            ])
            st.code(note, language="text")
            copy_to_clipboard_ui(note, "복사(NOAC 비교 요약)", "copy_noac_all")

    # ---------------------------
    # ELAN timing
    # ---------------------------
    with t2:
        st.subheader("ELAN 기반 DOAC 시작 시점 추천")
        st.write("병변 개수(1–4개)를 선택하고, 병변마다 최소 정보만 입력하시면 자동 분류하여 권고 시간을 표시합니다.")
        n_lesions = st.selectbox("병변 개수", [1, 2, 3, 4], index=0)

        lesions = []
        lesion_rows = []

        for i in range(int(n_lesions)):
            st.markdown(f"##### 병변 {i+1}")
            c1, c2, c3 = st.columns([1.2, 3.3, 1.5])

            with c1:
                circ = st.selectbox(f"순환계(병변 {i+1})", ["전순환계", "후순환계"], key=f"elan_circ_{i}")

            with c2:
                if circ == "후순환계":
                    posterior_site = st.selectbox(
                        f"부위(병변 {i+1})",
                        ["뇌간", "소뇌", "후대뇌동맥 피질 표재 가지", "기타 후순환계"],
                        key=f"elan_post_site_{i}"
                    )
                    anterior_pattern = "해당 없음"
                    anterior_major_pattern = "해당 없음"
                    anterior_multiterritory = False
                else:
                    posterior_site = "해당 없음"
                    anterior_pattern = st.selectbox(
                        f"중등도 판정 패턴(병변 {i+1})",
                        [
                            "해당 없음(크기 기준)",
                            "중대뇌동맥 피질 표재 가지",
                            "중대뇌동맥 심부 가지",
                            "경계영역(internal borderzone)",
                            "전대뇌동맥 피질 표재 가지",
                        ],
                        key=f"elan_ant_pat_{i}",
                    )
                    anterior_major_pattern = st.selectbox(
                        f"중증 판정 패턴(병변 {i+1})",
                        [
                            "해당 없음",
                            "전체 영역 침범",
                            "피질 표재 가지 2개 이상",
                            "피질 표재 가지 + 심부 가지 동반",
                        ],
                        key=f"elan_ant_major_{i}",
                    )
                    anterior_multiterritory = st.checkbox(f"2개 이상 동맥영역 동시 침범(병변 {i+1})", key=f"elan_multi_{i}")

            with c3:
                size_gt_1_5 = st.checkbox(f"최대 크기 >1.5cm (병변 {i+1})", key=f"elan_sizegt_{i}")

            sev = elan_severity_for_lesion(
                circ=circ,
                size_gt_1_5=size_gt_1_5,
                anterior_pattern=anterior_pattern,
                posterior_site=posterior_site,
                anterior_multiterritory=anterior_multiterritory,
                anterior_major_pattern=anterior_major_pattern,
            )

            lesions.append(sev)
            lesion_rows.append(
                {
                    "Lesion": i + 1,
                    "Circulation": circ,
                    "Pattern/Site": posterior_site if circ == "후순환계" else f"{anterior_pattern} / {anterior_major_pattern}",
                    "Size >1.5cm": size_gt_1_5,
                    "Severity": sev,
                }
            )

        overall = elan_overall_severity(lesions)
        reco = elan_recommendation(overall)

        st.divider()
        st.success(f"Infarct pattern severity는 {overall}입니다.")
        st.info(f"조기 시작 권고는 {reco}입니다.")
        st.dataframe(pd.DataFrame(lesion_rows), use_container_width=True)

        # figure: 무조건 로딩 시도
        st.markdown("#### ELAN 참고 그림")
        if Path("elan_figure.png").exists():
            st.image("elan_figure.png", use_container_width=True)
        else:
            st.info("같은 폴더에 `elan_figure.png` 파일을 두시면 자동으로 표시됩니다.")

        elan_note = (
            f"ELAN infarct pattern: {overall}\n"
            f"Recommended early DOAC initiation: {reco}\n"
            f"Rule applied: 2 minor -> moderate, 2 moderate -> major\n"
        )
        st.code(elan_note, language="text")
        copy_to_clipboard_ui(elan_note, "복사(ELAN 결과)", "copy_elan")

    # ---------------------------
    # MAGIC mechanism
    # ---------------------------
    with t3:
        st.subheader("MAGIC 기반 mechanism 분류(단계형 입력)")
        st.write("선택에 따라 다음 질문이 나타나도록 구성되어 있습니다.")

        if "magic_step" not in st.session_state:
            reset_magic()
        if st.button("MAGIC 입력을 초기화합니다."):
            reset_magic()
            st.rerun()

        a = st.session_state.magic_answers
        step = st.session_state.magic_step

        if step == 0:
            st.markdown("### 1단계")
            other = st.radio("명확한 다른 원인이 설명 가능한가요?", ["아니요", "예"], horizontal=True)
            a["other_determined"] = (other == "예")
            if st.button("다음 단계로 진행합니다."):
                st.session_state.magic_step = 99 if a["other_determined"] else 1
                st.rerun()

        if step == 1:
            st.markdown("### 2단계")
            lac = st.radio("Lacunar pattern이 의심되나요?", ["아니요", "예"], horizontal=True)
            a["lacunar"] = (lac == "예")
            if st.button("다음 단계로 진행합니다."):
                st.session_state.magic_step = 2
                st.rerun()

        if step == 2:
            st.markdown("### 3단계")
            rel = st.radio("Relevant artery lesion(관련 혈관 병변)이 있나요?", ["아니요", "예"], horizontal=True)
            a["relevant_artery"] = (rel == "예")

            if a["relevant_artery"] and a.get("lacunar"):
                br = st.radio("Branch atheroma/branch disease가 의심되나요?", ["아니요", "예"], horizontal=True)
                a["branch_atheroma"] = (br == "예")
            else:
                a["branch_atheroma"] = False

            if a["relevant_artery"] and (not a.get("lacunar")):
                ng = st.radio("Non-generic LAA pattern(특이 패턴)에 해당하나요?", ["아니요", "예"], horizontal=True)
                a["non_generic_pattern"] = (ng == "예")
            else:
                a["non_generic_pattern"] = False

            if st.button("다음 단계로 진행합니다."):
                st.session_state.magic_step = 3
                st.rerun()

        if step == 3:
            st.markdown("### 4단계")
            ce = st.radio("Cardioembolic source가 있나요(Hx/ECG/검사)?", ["아니요", "예"], horizontal=True)
            a["ce_source"] = (ce == "예")
            if a["ce_source"]:
                hr = st.radio("High-risk CE로 판단되나요?", ["아니요", "예"], horizontal=True)
                a["ce_high_risk"] = (hr == "예")
            else:
                a["ce_high_risk"] = False

            if st.button("결과를 확인합니다."):
                st.session_state.magic_step = 99
                st.rerun()

        if step == 99:
            mech = magic_result_from_answers(a)
            st.success(f"예측 mechanism은 '{mech}'입니다.")

            st.markdown("#### MAGIC 참고 그림")
            if Path("magic_figure.png").exists():
                st.image("magic_figure.png", use_container_width=True)
            else:
                st.info("같은 폴더에 `magic_figure.png` 파일을 두시면 자동으로 표시됩니다.")

            magic_note = (
                f"MAGIC mechanism classification: {mech}\n"
                f"- other_determined={a.get('other_determined')}, lacunar={a.get('lacunar')}, relevant_artery={a.get('relevant_artery')}, "
                f"branch_atheroma={a.get('branch_atheroma')}, non_generic_pattern={a.get('non_generic_pattern')}, "
                f"CE_source={a.get('ce_source')}, CE_high_risk={a.get('ce_high_risk')}\n"
            )
            st.code(magic_note, language="text")
            copy_to_clipboard_ui(magic_note, "복사(MAGIC 결과)", "copy_magic")

    # ---------------------------
    # Dyslipidemia (ASCVD risk estimation / LDL target)
    # ---------------------------
    with t4:
        st.subheader("Dyslipidemia")
        st.write("아래에서 ASCVD 위험도 추정과 LDL 목표/치료 전략을 분리하여 확인하실 수 있습니다.")

        asc_tab, ldl_tab = st.tabs(["🧾 ASCVD risk estimation", "🎯 LDL target"])

        # ========== ASCVD RISK ==========
        with asc_tab:
            st.markdown("### 1) 임상적 ASCVD 사건 횟수를 입력해 주십시오.")
            col1, col2, col3 = st.columns(3)
            with col1:
                n_mi = st.number_input("심근경색(MI) 횟수", 0, 20, 0, 1, key="n_mi")
            with col2:
                n_stroke = st.number_input("허혈성 뇌졸중/TIA 횟수", 0, 20, 0, 1, key="n_stroke")
            with col3:
                n_pad = st.number_input("말초동맥질환(PAD) 사건 횟수", 0, 20, 0, 1, key="n_pad")

            has_ascvd = (n_mi + n_stroke + n_pad) > 0
            major_events_count = n_mi + n_stroke + n_pad

            st.divider()
            st.markdown("### 2) AHA/ACC very-high-risk 판단(이차예방)")
            st.write("High-risk conditions는 체크박스로 선택해 주십시오.")
            checks = []
            cA, cB, cC, cD = st.columns(4)
            cols = [cA, cB, cC, cD]
            for idx, label in enumerate(AHA_HR_CONDITIONS_CHECK):
                with cols[idx % 4]:
                    checks.append(st.checkbox(label, key=f"aha_hr_{idx}"))
            aha_hr_count = sum(1 for x in checks if x)

            very_high = aha_very_high_risk(major_events_count, aha_hr_count) if has_ascvd else False
            st.info(f"Major ASCVD 사건 개수는 {major_events_count}개입니다.")
            st.info(f"High-risk conditions 체크 개수는 {aha_hr_count}개입니다.")
            st.success(f"AHA/ACC very-high-risk 여부는 {'예' if very_high else '아니오'}입니다.")

            st.divider()
            st.markdown("### 3) AHA 10-year ASCVD Risk (Pooled Cohort Equations) 계산")
            st.write("구성요소를 입력하시면 10-year ASCVD risk(%)를 계산하여 표시합니다.")
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                pce_sex = st.selectbox("성별", ["Male", "Female"], key="pce_sex")
            with c2:
                pce_race = st.selectbox("인종(계수용)", ["White", "African American"], key="pce_race")
            with c3:
                pce_age = st.number_input("나이(세)", 20, 79, 60, 1, key="pce_age")
            with c4:
                pce_smoker = st.checkbox("현재 흡연", key="pce_smoker")

            c5, c6, c7, c8 = st.columns(4)
            with c5:
                pce_tc = st.number_input("Total cholesterol (mg/dL)", 80, 400, 200, 1, key="pce_tc")
            with c6:
                pce_hdl = st.number_input("HDL-C (mg/dL)", 10, 120, 50, 1, key="pce_hdl")
            with c7:
                pce_sbp = st.number_input("Systolic BP (mmHg)", 80, 240, 130, 1, key="pce_sbp")
            with c8:
                pce_bp_treated = st.checkbox("혈압약 복용 중(HTN treatment)", key="pce_bp_treated")

            pce_dm = st.checkbox("당뇨병", key="pce_dm")

            pce_risk = pce_10y_risk_percent(
                sex=pce_sex,
                race=pce_race,
                age=float(pce_age),
                tc=float(pce_tc),
                hdl=float(pce_hdl),
                sbp=float(pce_sbp),
                bp_treated=bool(pce_bp_treated),
                smoker=bool(pce_smoker),
                diabetes=bool(pce_dm),
            )
            if pce_risk is None:
                st.warning("입력값을 확인해 주십시오.")
            else:
                st.success(f"AHA 10-year ASCVD risk 추정치는 약 {pce_risk:.1f}%입니다.")

            st.divider()
            st.markdown("### 4) ESC SCORE2(또는 SCORE2-OP) 10-year CVD risk 계산(추정치)")
            st.write("정확한 공식 계산기와 동일한 정밀도는 보장되지 않으며, 교육/보조 목적의 추정치입니다.")
            r1, r2, r3, r4, r5 = st.columns(5)
            with r1:
                s2_age = st.number_input("나이(세)", 40, 89, 65, 1, key="s2_age")
            with r2:
                s2_sex = st.selectbox("성별", ["남성", "여성"], key="s2_sex")
            with r3:
                s2_smoker = st.checkbox("현재 흡연", key="s2_smoke")
            with r4:
                s2_sbp = st.number_input("SBP(mmHg)", 80, 240, 130, 1, key="s2_sbp")
            with r5:
                s2_nonhdl = st.number_input("non-HDL-C (mg/dL)", 50, 400, 150, 1, key="s2_nonhdl")

            s2_region = st.selectbox("국가 리스크 클러스터(HeartScore 기준)", ["Low", "Moderate", "High", "Very high"], key="s2_region")

            score2_pct = score2_estimate_percent(s2_age, s2_sex, s2_smoker, s2_sbp, s2_nonhdl, s2_region)
            esc_cat_from_score = esc_risk_category_from_score2(score2_pct)
            st.success(f"ESC SCORE2(추정) 10-year CVD risk는 약 {score2_pct:.1f}%이며, 컷오프 기준 위험군은 {esc_cat_from_score}입니다.")

            asc_summary = "\n".join([
                "ASCVD risk summary",
                f"- Events: MI={n_mi}, Stroke/TIA={n_stroke}, PAD={n_pad} (total major events={major_events_count})",
                f"- AHA/ACC very-high-risk: {'Yes' if very_high else 'No'}",
                f"- AHA high-risk conditions checked: {aha_hr_count}",
                f"- AHA PCE 10y risk (estimate): {pce_risk:.1f}%" if pce_risk is not None else "- AHA PCE risk: N/A",
                f"- ESC SCORE2 (estimate): {score2_pct:.1f}% (region={s2_region})",
                f"- ESC SCORE2 category by cutoff: {esc_cat_from_score}",
            ])
            st.code(asc_summary, language="text")
            copy_to_clipboard_ui(asc_summary, "복사(ASCVD 위험도 요약)", "copy_ascvd_risk")

        # ========== LDL TARGET ==========
        with ldl_tab:
            st.markdown("### 1) 현재 LDL-C 및 치료 상태를 입력해 주십시오.")
            ldl_now = st.number_input("현재 LDL-C (mg/dL)", 10, 400, 100, 1, key="ldl_now")
            on_hi = st.checkbox("고강도 스타틴 또는 최대내약용량 스타틴을 사용 중입니다.", key="on_hi")
            on_eze = st.checkbox("Ezetimibe를 병용 중입니다.", key="on_eze")
            on_pcsk9 = st.checkbox("PCSK9 억제제를 사용 중입니다.", key="on_pcsk9")

            st.divider()
            st.markdown("### 2) AHA/ACC 기준: 치료 강화 역치(threshold) 및 단계")
            if has_ascvd:
                aha_threshold = 55 if very_high else 70
                st.info(f"임상적 ASCVD가 있으므로 치료 강화 역치는 LDL-C {aha_threshold} mg/dL를 기준으로 판단합니다.")
                aha_actions = []
                if not on_hi:
                    aha_actions.append("고강도 스타틴 또는 최대내약용량 스타틴으로 최적화하시는 것을 고려하실 수 있습니다.")
                if ldl_now >= aha_threshold:
                    if not on_eze:
                        aha_actions.append(f"LDL-C가 {aha_threshold} mg/dL 이상이므로 ezetimibe 추가를 고려하실 수 있습니다.")
                    elif not on_pcsk9:
                        aha_actions.append(f"ezetimibe 병용에도 LDL-C가 {aha_threshold} mg/dL 이상이면 PCSK9 억제제 추가를 고려하실 수 있습니다.")
                    else:
                        aha_actions.append("PCSK9 억제제까지 사용 중이면 순응도/2차 원인/다른 옵션을 재평가하시는 것이 합리적입니다.")
                else:
                    aha_actions.append(f"LDL-C가 {aha_threshold} mg/dL 미만이면 현재 전략을 유지하며 추적하실 수 있습니다.")
            else:
                aha_threshold = None
                st.warning("임상적 ASCVD가 없는 경우에는 10-year ASCVD risk(PCE)를 기반으로 스타틴 적응증 및 강도를 결정하는 접근이 일반적입니다.")
                aha_actions = [
                    "10-year ASCVD risk를 참고하여 치료 강도를 결정하실 수 있습니다.",
                    "LDL-C가 매우 높거나 가족력/다중 위험인자가 있으면 더 적극적 치료를 고려하실 수 있습니다.",
                ]

            for a in aha_actions:
                st.write(f"- {a}")

            st.divider()
            st.markdown("### 3) ESC/EAS 기준: 위험군별 LDL-C 목표(target) 및 치료 강화 단계")
            st.write("ESC 위험군은 (1) documented ASCVD 여부 + (2) SCORE2 컷오프 및 주요 동반질환으로 결정되는 경우가 많습니다.")

            # 간단 분류(secondary prevention 우선): ASCVD 있으면 very high로 둠
            # 반복사건(2년 이내) 입력
            esc_recurrent = st.checkbox("최대치료에도 2년 이내 재발 사건(recurrent ASCVD)이 있었습니다.", key="esc_recur_ldl")
            if has_ascvd and esc_recurrent:
                esc_cat = "Very high (recurrent within 2y)"
            elif has_ascvd:
                esc_cat = "Very high"
            else:
                # ASCVD 없으면 SCORE2(추정)로 위험군 컷오프 분류를 사용
                esc_cat = esc_cat_from_score

            esc_target = esc_ldl_target_by_category(esc_cat if esc_cat != "Very high (recurrent within 2y)" else "Very high (recurrent within 2y)")
            st.info(f"ESC/EAS 위험군은 '{esc_cat}'이며, LDL 목표치는 {esc_target}입니다.")

            esc_actions = []
            if esc_cat in ["Low", "Moderate"]:
                esc_actions.append("생활습관 교정이 기본이며, 위험도 및 LDL 수준에 따라 약물치료를 고려하실 수 있습니다.")
            else:
                esc_actions.append("고강도 스타틴 또는 최대내약용량 스타틴 치료를 우선 고려하실 수 있습니다.")
                esc_actions.append("목표 미달 시 ezetimibe 병용을 고려하실 수 있습니다.")
                esc_actions.append("목표 미달이 지속되면 PCSK9 억제제 추가를 고려하실 수 있습니다.")
                esc_actions.append("최근 ESC update에서는 목표(target)은 유지하면서도, 상황에 따라 조기 병용(ezetimibe 병용)을 합리적으로 고려할 수 있다는 방향성이 강조됩니다.")

            for a in esc_actions:
                st.write(f"- {a}")

            st.divider()
            st.markdown("### 4) AHA/ACC와 ESC/EAS 결과를 함께 정리합니다.")
            summary = "\n".join([
                "LDL strategy summary",
                f"- Current LDL-C: {ldl_now} mg/dL",
                f"- On high-intensity/max tolerated statin: {'Yes' if on_hi else 'No'}",
                f"- On ezetimibe: {'Yes' if on_eze else 'No'}",
                f"- On PCSK9 inhibitor: {'Yes' if on_pcsk9 else 'No'}",
                "",
                "[AHA/ACC]",
                f"- Clinical ASCVD: {'Yes' if has_ascvd else 'No'}",
                f"- Very-high-risk: {'Yes' if very_high else 'No'}",
                f"- Intensification threshold: {aha_threshold} mg/dL" if aha_threshold is not None else "- Primary prevention: risk-based approach",
                "Actions:",
                *[f"  • {x}" for x in aha_actions],
                "",
                "[ESC/EAS]",
                f"- Category: {esc_cat}",
                f"- LDL target: {esc_target}",
                "Actions:",
                *[f"  • {x}" for x in esc_actions],
            ])
            st.code(summary, language="text")
            copy_to_clipboard_ui(summary, "복사(LDL 전략 요약)", "copy_ldl_strategy")


# =========================================================
# 2) 가이드라인 및 근거
# =========================================================
with tab_ref:
    st.subheader("가이드라인 및 근거")
    st.write("계산기 및 알고리즘에 사용된 정의와 기준을 표와 설명으로 제공합니다.")

    g1, g2, g3, g4 = st.tabs(["📌 ABCD² / CHA₂DS₂-VASc", "⏱️ ELAN", "🧭 MAGIC", "🫀 Dyslipidemia (ESC/AHA)"])

    with g1:
        st.markdown("### ABCD² 점수 및 단기 뇌졸중 재발 위험(참고)")
        st.dataframe(ABCD2_RISK_TABLE, use_container_width=True)
        st.markdown("""
- ABCD²는 TIA 이후 단기 뇌졸중 재발 위험을 층화하는 점수입니다.  
- 실제 위험도는 코호트/진료 환경/치료 상황에 따라 달라질 수 있습니다.  
""")

        st.markdown("### CHA₂DS₂-VASc 점수 및 연간 뇌졸중/전신색전증 위험(참고)")
        st.dataframe(CHA2DS2_VASC_RISK_TABLE, use_container_width=True)
        st.markdown("""
- CHA₂DS₂-VASc는 비판막성 AF에서 항응고 필요성을 판단하는 도구로 널리 사용됩니다.  
- 연간 위험도 수치는 항응고 치료 여부, 코호트 특성 등에 따라 달라질 수 있습니다.  
""")

    with g2:
        st.markdown("### ELAN 알고리즘 기준(요약)")
        elan_df = pd.DataFrame(
            [
                {"Infarct Pattern": "Minor infarct (≤1.5 cm in any territory)", "Early initiation": "≤ 48시간"},
                {"Infarct Pattern": "Moderate infarct (예: MCA cortical branch, deep MCA branch, internal border zone, ACA/PCA cortical branch)", "Early initiation": "≤ 48시간"},
                {"Infarct Pattern": "Major infarct (예: entire territory, multiple territories, large posterior lesion 등)", "Early initiation": "6–7일"},
            ]
        )
        st.dataframe(elan_df, use_container_width=True)
        st.markdown("#### 참고 그림")
        if Path("elan_figure.png").exists():
            st.image("elan_figure.png", use_container_width=True)
        else:
            st.info("같은 폴더에 `elan_figure.png` 파일을 두시면 자동으로 표시됩니다.")

    with g3:
        st.markdown("### MAGIC 알고리즘(단계형 구현)")
        st.markdown("""
- 본 애플리케이션의 MAGIC 파트는 사용 편의성을 위해 단계형 질문 방식으로 구현되어 있습니다.  
- 선택에 따라 다음 질문이 나타납니다.  
""")
        st.markdown("#### 참고 그림")
        if Path("magic_figure.png").exists():
            st.image("magic_figure.png", use_container_width=True)
        else:
            st.info("같은 폴더에 `magic_figure.png` 파일을 두시면 자동으로 표시됩니다.")

    with g4:
        st.markdown("## ESC/EAS 2025 Focused Update 기반 핵심 근거(상세)")
        st.markdown("""
### 1) ESC/EAS에서 ‘Documented ASCVD(임상 또는 영상으로 확실한 ASCVD)’ 정의
- ESC 2025 Focused Update의 Table 3에서 very-high-risk 조건으로 “Documented ASCVD”를 명시합니다.  
- Documented ASCVD에는 다음이 포함됩니다:  
  - 이전 ACS(심근경색 또는 불안정 협심증)  
  - chronic coronary syndromes  
  - coronary revascularization(PCI, CABG, 기타 혈관 재개통술)  
  - stroke 및 TIA  
  - peripheral arterial disease  
- 또한 영상에서 확실한 ASCVD(관상동맥 CT/조영술 유의미 플라크, 경동맥/대퇴동맥 초음파 플라크, CAC 현저히 상승 등)도 포함됩니다.
""")

        esc_def_table = pd.DataFrame([{"ESC documented ASCVD 예시": x} for x in ESC_DOC_ASCVDS])
        st.dataframe(esc_def_table, use_container_width=True)

        st.markdown("""
### 2) SCORE2/SCORE2-OP 컷오프 기반 위험군(ESC 2025 Table 3 요지)
- Very high risk: SCORE2 또는 SCORE2-OP ≥20%  
- High risk: ≥10% and <20%  
- Moderate risk: ≥2% and <10%  
- Low risk: <2%
""")

        st.markdown("""
### 3) Risk modifiers(추가 위험 수정자) 예시(ESC 2025 Box 1 요지)
- 가족력(조기 CVD), 고위험 인종, 스트레스/사회적 박탈, 비만/운동부족, 만성 염증성 질환, 정신질환, OSA 등  
- hs-CRP 상승, Lp(a) 상승 등
""")

        st.markdown("""
### 4) 위험도/LDL 수준에 따른 중재 전략(ESC 2025 Table 4 요지)
- 위험도와 ‘치료 전 LDL-C’ 수준에 따라 생활요법만, 생활요법+약물 고려, 또는 생활요법+동반 약물치료를 제시합니다.  
- 특히 고위험/초고위험에서는 비교적 낮은 LDL 구간에서도 약물치료 병행을 권고하는 방향성이 나타납니다.
""")
