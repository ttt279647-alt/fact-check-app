import os
import json
import re
from datetime import datetime
import streamlit as st
import requests
from google import genai
from google.genai import types

# ページ基本設定
st.set_page_config(page_title="SNS記事ファクトチェック作成機", layout="wide")
st.title("📱 SNS記事 ファクトチェッカー")

# 使用モデル
MODEL_NAME = "gemini-3.8-flash"

# プラットフォーム仕様
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

    st.divider()
    api_key = st.text_input("Gemini API Key", type="password", value=os.environ.get("GEMINI_API_KEY", ""))
    target_score = st.slider("採用基準スコア（%）", min_value=70, max_value=100, value=95)
    
    st.divider()
    st.subheader("📊 スプレッドシート連携")
    gas_webhook_url = st.text_input(
        "GAS Webhook URL",
        placeholder="https://script.google.com/macros/s/.../exec",
        help="Google Apps ScriptでデプロイしたWebアプリURLを貼り付けてください。"
    )
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

# 生成 & チェック実行ボタン
if st.button("🚀 記事生成 & ファクトチェック開始", type="primary"):
    if not theme.strip():
        st.warning("投稿テーマを入力してください。")
        st.stop()

    # 連続作成時のゴースト防止：前回データをクリア
    keys_to_clear = [
        "main_text", "reply_text", "check_result", "full_text",
        "grounding_sources", "grounding_queries", "raw_verify_response",
        "draft_prompt_sent", "verify_prompt_sent", "edit_main", "edit_reply"
    ]
    for key in keys_to_clear:
        st.session_state.pop(key, None)

    preset = PLATFORM_PRESETS[selected_platform]

    # --- Step 1: Web検索を活用した記事ドラフト生成 ---
    status_placeholder = st.empty()
    status_placeholder.info(f"1/2: Google検索で最新情報をリサーチしつつ、{selected_platform}向けに記事ドラフトを生成中...")

    if selected_platform == "X (旧Twitter)" and x_include_reply:
        draft_prompt = f"""Google検索を活用して以下のテーマに関する最新の客観的事実やデータを調査し、
X(旧Twitter)向けのツリー形式投稿（親ポスト＋自身のリプライ）を作成してください。
必ず以下のJSON形式のみを出力してください（Markdownコードブロックは不要）。

{{
  "main_post": "1ポスト目の本文（120〜200文字程度。結論やフック、最新データの要約）",
  "reply_post": "自身のリプライ欄の本文（140〜240文字程度。詳細データ、背景、考察など）"
}}

【テーマ】:
{theme}"""
        try:
            draft_res = client.models.generate_content(
                model=MODEL_NAME,
                contents=draft_prompt,
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())]
                )
            )
            draft_json = extract_json(draft_res.text)
            st.session_state["main_text"] = draft_json.get("main_post", "")
            st.session_state["reply_text"] = draft_json.get("reply_post", "")
            st.session_state["is_tree_mode"] = True
            full_text = f"【1ポスト目】\n{st.session_state['main_text']}\n\n【リプライ欄】\n{st.session_state['reply_text']}"
        except Exception as e:
            status_placeholder.empty()
            st.error(f"ドラフト生成でエラーが発生しました: {e}")
            st.stop()
    else:
        draft_prompt = f"""Google検索を活用して以下のテーマに関する最新の客観的事実やデータを調査し、
{preset['prompt_instruction']}
具体的な数値、年代、固有名詞、客観的事実を積極的に盛り込んでください。

【テーマ】:
{theme}"""
        try:
            draft_res = client.models.generate_content(
                model=MODEL_NAME,
                contents=draft_prompt,
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())]
                )
            )
            st.session_state["main_text"] = draft_res.text
            st.session_state["reply_text"] = ""
            st.session_state["is_tree_mode"] = False
            full_text = draft_res.text
        except Exception as e:
            status_placeholder.empty()
            st.error(f"ドラフト生成でエラーが発生しました: {e}")
            st.stop()

    st.session_state["draft_prompt_sent"] = draft_prompt
    st.session_state["full_text"] = full_text
    st.session_state["platform_used"] = selected_platform
    st.session_state["input_theme"] = theme

    # --- Step 2: Google検索連動ファクトチェック ---
    status_placeholder.info("2/2: 生成された記事の事実関係をGoogle検索で二重検証中...")

    verify_prompt = f"""あなたは厳格なファクトチェッカーです。
以下のテキストに含まれる「具体的な事実（年号、数値、名称、因果関係など）」を抽出して検証してください。
Google検索ツールを活用して各事実の裏取りを行い、必ず以下のJSONスキーマの形式のみを出力してください。
各検証項目には、裏取りの根拠としたWebサイトのURLと記事タイトルを必ず含めてください。

{{
  "claims": [
    {{
      "claim": "検証対象の具体的な主張",
      "status": "PASS" または "FAIL" または "UNKNOWN",
      "reason": "判定理由（検索結果に基づく根拠）",
      "source_title": "参照したWebサイトのタイトル（不明な場合は空文字）",
      "source_url": "裏取りに使用したWebページのURL（不明な場合は空文字）"
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

        sources = []
        queries = []
        if verify_res.candidates and len(verify_res.candidates) > 0:
            candidate = verify_res.candidates[0]
            if hasattr(candidate, "grounding_metadata") and candidate.grounding_metadata:
                gm = candidate.grounding_metadata
                if hasattr(gm, "web_search_queries") and gm.web_search_queries:
                    queries = list(gm.web_search_queries)
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

    # 1. 各クレームごとの判定理由と個別リンク
    with st.expander("🔍 検証された事実項目と裏取り根拠（個別リンク付き）", expanded=not is_passed):
        if not claims:
            st.write("検証対象となる客観的クレーム（数値や固有名詞など）は検出されませんでした。")
        for i, c in enumerate(claims):
            status = c.get("status")
            status_icon = "✅ PASS" if status == "PASS" else ("❌ FAIL" if status == "FAIL" else "⚠️ UNKNOWN")
            st.markdown(f"**[{status_icon}] 事実 {i+1}:** {c.get('claim')}")
            st.caption(f"判定理由: {c.get('reason')}")
            
            source_url = c.get("source_url")
            source_title = c.get("source_title") or "裏取り参照元Webサイト"
            if source_url and source_url.startswith("http"):
                st.markdown(f"🔗 **参照ソース:** [{source_title}]({source_url})")
            st.markdown("---")

    # 2. 実行されたGoogle検索クエリと取得元リスト
    with st.expander("🌐 Google検索の実行ログ・全体ソース一覧", expanded=False):
        queries = st.session_state.get("grounding_queries", [])
        if queries:
            st.markdown("**実行された検索クエリ:**")
            st.write(" / ".join([f"`{q}`" for q in queries]))

        sources = st.session_state.get("grounding_sources", [])
        if sources:
            st.markdown("**検出されたWebソース一覧:**")
            for s in sources:
                st.markdown(f"- [{s['title']}]({s['url']})")

    st.divider()

    # 人間による手直しエリア
    st.subheader("✍️ 記事の手直し（Human-in-the-Loop）")
    if st.session_state.get("is_tree_mode", False):
        col_main, col_reply = st.columns(2)
        with col_main:
            st.markdown("**1️⃣ 親ポスト（メイン）**")
            final_main = st.text_area("親ポストの編集", value=st.session_state["main_text"], height=200, key="edit_main")
            st.caption(f"文字数: {len(final_main)} 文字")

        with col_reply:
            st.markdown("**2️⃣ リプライ欄（補足・展開）**")
            final_reply = st.text_area("リプライの編集", value=st.session_state["reply_text"], height=200, key="edit_reply")
            st.caption(f"文字数: {len(final_reply)} 文字")
    else:
        final_main = st.text_area(
            "下書きエディタ（編集内容をそのままコピーできます）",
            value=st.session_state["main_text"],
            height=220,
            key="edit_main"
        )
        final_reply = ""
        st.caption(f"文字数: {len(final_main)} 文字")

    st.divider()

    # プロンプト確認用デバッグエリア
    with st.expander("🛠️ 実行ログ・送信プロンプトを確認する（デバッグ用）"):
        st.markdown("#### 1. 記事ドラフト生成に送信したプロンプト")
        st.code(st.session_state.get("draft_prompt_sent", ""), language="text")

        st.markdown("#### 2. ファクトチェックに送信したプロンプト")
        st.code(st.session_state.get("verify_prompt_sent", ""), language="text")

        st.markdown("#### 3. ファクトチェックAPIからの生レスポンス（Raw JSON）")
        st.code(st.session_state.get("raw_verify_response", ""), language="json")

    st.divider()

    # --- スプレッドシート保存ボタン ---
    st.subheader("💾 記録の保存")
    if st.button("📥 スプレッドシートに保存する", type="secondary"):
        if not gas_webhook_url:
            st.error("👈 左側のサイドバーに『GAS Webhook URL』を入力してください。")
        else:
            # 1. クレーム一覧を整形
            claims_formatted = []
            for i, c in enumerate(claims):
                s_url = f" ({c.get('source_url')})" if c.get("source_url") else ""
                claims_formatted.append(f"[{c.get('status')}] {c.get('claim')}\n理由: {c.get('reason')}{s_url}")
            claims_text = "\n\n".join(claims_formatted)

            # 2. 検索ソース・クエリを整形
            sources_list = [f"- {s['title']}: {s['url']}" for s in st.session_state.get("grounding_sources", [])]
            queries_str = "クエリ: " + " / ".join(st.session_state.get("grounding_queries", []))
            sources_text = queries_str + "\n" + "\n".join(sources_list)

            # 3. プロンプトログを整形
            prompts_text = f"【ドラフト生成プロンプト】\n{st.session_state.get('draft_prompt_sent', '')}\n\n【ファクトチェックプロンプト】\n{st.session_state.get('verify_prompt_sent', '')}"

            payload = {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "platform": st.session_state.get("platform_used", ""),
                "theme": st.session_state.get("input_theme", ""),
                "main_post": final_main,
                "reply_post": final_reply,
                "claims_text": claims_text,
                "sources_text": sources_text,
                "prompts_text": prompts_text,
                "raw_verify_response": st.session_state.get("raw_verify_response", "")
            }

            try:
                headers = {
                    "Content-Type": "application/json",
                    "User-Agent": "Mozilla/5.0"
                }
                res = requests.post(
                    gas_webhook_url,
                    data=json.dumps(payload),
                    headers=headers,
                    allow_redirects=True,
                    timeout=15
                )
                if res.status_code == 200:
                    st.success("✅ スプレッドシートへの保存が完了しました！")
                else:
                    st.error(f"保存に失敗しました（ステータスコード: {res.status_code} / 応答: {res.text}）")
            except Exception as e:
                st.error(f"通信エラーが発生しました: {e}")