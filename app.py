import os
import json
import re
import streamlit as st
from google import genai
from google.genai import types

# ページ設定
st.set_page_config(page_title="SNS記事ファクトチェック作成機", layout="wide")
st.title("📱 SNS記事 ファクトチェッカー")

MODEL_NAME = "gemini-3.8-flash"

PLATFORM_PRESETS = {
    "X (旧Twitter)": {
        "desc": "短文・要点重視（140〜250文字程度）、ハッシュタグ控えめ、フックの効いた書き出し",
        "prompt_instruction": "X(旧Twitter)向けの投稿文を作成してください。文字数は140文字〜250文字程度。冒頭で惹きつけ、要点をシャープに伝えてください。ハッシュタグは1〜2個程度。"
    },
    "Threads": {
        "desc": "会話調・ストーリー性（200〜400文字程度）、改行を活かした読みやすさ重視",
        "prompt_instruction": "Threads向けの投稿文を作成してください。文字数は200文字〜400文字程度。共感や意見を促す会話的なトーンで、適度に改行を入れて読みやすくしてください。"
    },
    "Instagram (キャプション)": {
        "desc": "長文解説・視覚的装飾（300〜600文字程度）、絵文字や箇条書き、関連ハッシュタグ多数",
        "prompt_instruction": "Instagramの投稿キャプションを作成してください。文字数は300文字〜600文字程度。絵文字を効果的に使い、見出しや箇条書きで分かりやすく整理してください。末尾に関連するハッシュタグを5〜8個付けてください。"
    }
}

# --- サイドバー設定 ---
with st.sidebar:
    st.header("⚙️ 設定")
    selected_platform = st.selectbox(
        "投稿先プラットフォーム",
        options=list(PLATFORM_PRESETS.keys()),
        index=0
    )
    st.caption(f"💡 {PLATFORM_PRESETS[selected_platform]['desc']}")

    x_include_reply = False
    if selected_platform == "X (旧Twitter)":
        x_include_reply = st.checkbox("🔗 リプライ欄（補足・ツリー）も一緒に作成する", value=True)
        if x_include_reply:
            st.caption("※1投稿目で要約をフックにし、リプライ欄で詳細や考察を展開します。")

    st.divider()
    api_key = st.text_input("Gemini API Key", type="password", value=os.environ.get("GEMINI_API_KEY", ""))
    target_score = st.slider("採用基準スコア（%）", min_value=70, max_value=100, value=95)
    st.caption(f"使用モデル: `{MODEL_NAME}`")

if not api_key:
    st.warning("👈 左側のサイドバーにGemini APIキーを入力してください。")
    st.stop()

client = genai.Client(api_key=api_key)

def extract_json(text):
    text = re.sub(r'```json\s*', '', text)
    text = re.sub(r'```\s*', '', text)
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1:
        return json.loads(text[start:end+1])
    return json.loads(text)

# --- メインエリア ---
st.subheader(f"📝 記事の作成 ({selected_platform} 向け)")
theme = st.text_area(
    "投稿テーマや盛り込みたい要点",
    placeholder="例：カーボン入りランニングシューズの有効性について",
    height=100
)

if st.button("🚀 記事生成 & ファクトチェック開始", type="primary"):
    if not theme.strip():
        st.warning("投稿テーマを入力してください。")
        st.stop()

    preset = PLATFORM_PRESETS[selected_platform]

    # --- Step 1: 記事ドラフト生成 ---
    status_placeholder = st.empty()
    status_placeholder.info(f"1/2: {selected_platform}向けに記事ドラフトを生成中...")

    if selected_platform == "X (旧Twitter)" and x_include_reply:
        draft_prompt = f"""X(旧Twitter)向けのツリー形式投稿（親ポスト＋自身のリプライ）を作成してください。
以下のJSON形式で出力してください。
{{
  "main_post": "1ポスト目の本文（120〜200文字程度。結論やフック、要約）",
  "reply_post": "自身のリプライ欄の本文（140〜240文字程度。詳細データ、背景、考察など）"
}}
【テーマ】: {theme}"""
        draft_res = client.models.generate_content(
            model=MODEL_NAME,
            contents=draft_prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )
        draft_json = extract_json(draft_res.text)
        st.session_state["main_text"] = draft_json.get("main_post", "")
        st.session_state["reply_text"] = draft_json.get("reply_post", "")
        st.session_state["is_tree_mode"] = True
        full_text = f"【1ポスト目】\n{st.session_state['main_text']}\n\n【リプライ欄】\n{st.session_state['reply_text']}"
    else:
        draft_prompt = f"{preset['prompt_instruction']}\n具体的な数値、年代、固有名詞を含めてください。\n\n【テーマ】: {theme}"
        draft_res = client.models.generate_content(model=MODEL_NAME, contents=draft_prompt)
        st.session_state["main_text"] = draft_res.text
        st.session_state["reply_text"] = ""
        st.session_state["is_tree_mode"] = False
        full_text = draft_res.text

    st.session_state["draft_prompt_sent"] = draft_prompt
    st.session_state["full_text"] = full_text
    st.session_state["platform_used"] = selected_platform

    # --- Step 2: Google検索連動ファクトチェック ---
    status_placeholder.info("2/2: Google検索を実行して事実関係を検証中...")

    verify_prompt = f"""あなたは厳格なファクトチェッカーです。
以下のテキストに含まれる「具体的な事実（年号、数値、名称、因果関係など）」を抽出して検証してください。
Google検索を活用して各事実の裏取りを行い、必ず以下のJSONスキーマの形式のみを出力してください。

{{
  "claims": [
    {{
      "claim": "検証対象の具体的な主張",
      "status": "PASS" または "FAIL" または "UNKNOWN",
      "reason": "判定理由（検索結果に基づく根拠）"
    }}
  ]
}}

【検証対象テキスト】:
{full_text}"""

    st.session_state["verify_prompt_sent"] = verify_prompt

    try:
        verify_res = client.models.generate_content(
            model=MODEL_NAME,
            contents=verify_prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())]
            )
        )
        st.session_state["raw_verify_response"] = verify_res.text
        st.session_state["check_result"] = extract_json(verify_res.text)

        # ★ Google検索の実際の参照元（ソースURL）と検索ワードを抽出
        sources = []
        queries = []
        if verify_res.candidates and len(verify_res.candidates) > 0:
            candidate = verify_res.candidates[0]
            if hasattr(candidate, "grounding_metadata") and candidate.grounding_metadata:
                gm = candidate.grounding_metadata
                # 実行された検索クエリ
                if hasattr(gm, "web_search_queries") and gm.web_search_queries:
                    queries = list(gm.web_search_queries)
                # 参照した実際のWebページ
                if hasattr(gm, "grounding_chunks") and gm.grounding_chunks:
                    for chunk in gm.grounding_chunks:
                        if hasattr(chunk, "web") and chunk.web:
                            sources.append({
                                "title": getattr(chunk.web, "title", "Webソース"),
                                "url": getattr(chunk.web, "uri", "#")
                            })

        st.session_state["grounding_sources"] = sources
        st.session_state["grounding_queries"] = queries
        status_placeholder.empty()
    except Exception as e:
        status_placeholder.empty()
        st.error(f"ファクトチェックの解析に失敗しました: {e}")
        st.stop()

# --- 結果の表示と手直し画面 ---
if "main_text" in st.session_state and "check_result" in st.session_state:
    claims = st.session_state["check_result"].get("claims", [])
    total_claims = len(claims)
    pass_claims = sum(1 for c in claims if c.get("status") == "PASS")
    fail_claims = sum(1 for c in claims if c.get("status") == "FAIL")

    score = (pass_claims / total_claims * 100) if total_claims > 0 else 0

    st.divider()
    st.subheader(f"📊 検証結果（{st.session_state.get('platform_used', '')}）")

    col1, col2, col3 = st.columns(3)
    col1.metric("事実適合スコア", f"{score:.1f}%")
    col2.metric("検証クレーム総数", f"{total_claims}件")
    col3.metric("誤り検出（FAIL）", f"{fail_claims}件")

    is_passed = (score >= target_score) and (fail_claims == 0)
    if is_passed:
        st.success(f"🎉 基準クリア！（{target_score}%以上 & 誤り0件） 下記で微調整してそのまま投稿できます。")
    else:
        st.error("⚠️ 基準未達または誤り（FAIL）が検出されました。内容を精査してください。")

    # 1. クレーム詳細のアコーディオン
    with st.expander("🔍 検証された事実項目と判定理由", expanded=not is_passed):
        if not claims:
            st.write("検証対象となる客観的クレーム（数値や固有名詞など）は検出されませんでした。")
        for i, c in enumerate(claims):
            status = c.get("status")
            status_icon = "✅ PASS" if status == "PASS" else ("❌ FAIL" if status == "FAIL" else "⚠️ UNKNOWN")
            st.markdown(f"**[{status_icon}] 事実 {i+1}:** {c.get('claim')}")
            st.caption(f"判定理由: {c.get('reason')}")

    # 2. ★ 実際に参照したWebソースと検索クエリの表示エリア
    with st.expander("🌐 実際に裏取りで使用したGoogle検索ソース・リンク一覧", expanded=True):
        queries = st.session_state.get("grounding_queries", [])
        if queries:
            st.markdown("**実行された検索クエリ:**")
            st.write(" / ".join([f"`{q}`" for q in queries]))

        sources = st.session_state.get("grounding_sources", [])
        if sources:
            st.markdown("**参照されたWebサイト（クリックして一次情報を確認できます）:**")
            for idx, s in enumerate(sources):
                st.markdown(f"- [{s['title']}]({s['url']})")
        else:
            st.caption("※参照ソース情報が取得できませんでした（一般的な一般常識の範囲として判定された可能性があります）。")

    st.divider()
    st.subheader("✍️ 記事の手直し（Human-in-the-Loop）")

    if st.session_state.get("is_tree_mode", False):
        col_main, col_reply = st.columns(2)
        with col_main:
            st.markdown("**1️⃣ 親ポスト（メイン）**")
            edit_main = st.text_area("親ポストの編集", value=st.session_state["main_text"], height=200, key="edit_main")
            st.caption(f"文字数: {len(edit_main)} 文字")

        with col_reply:
            st.markdown("**2️⃣ リプライ欄（補足・展開）**")
            edit_reply = st.text_area("リプライの編集", value=st.session_state["reply_text"], height=200, key="edit_reply")
            st.caption(f"文字数: {len(edit_reply)} 文字")
    else:
        final_text = st.text_area(
            "下書きエディタ（編集内容をそのままコピーできます）",
            value=st.session_state["main_text"],
            height=220
        )
        st.caption(f"文字数: {len(final_text)} 文字")

    st.divider()

    with st.expander("🛠️ 実行ログ・送信プロンプトを確認する（デバッグ用）"):
        st.markdown("#### 1. 記事ドラフト生成に送信したプロンプト")
        st.code(st.session_state.get("draft_prompt_sent", ""), language="text")

        st.markdown("#### 2. ファクトチェックに送信したプロンプト")
        st.code(st.session_state.get("verify_prompt_sent", ""), language="text")

        st.markdown("#### 3. ファクトチェックAPIからの生レスポンス（Raw JSON）")
        st.code(st.session_state.get("raw_verify_response", ""), language="json")