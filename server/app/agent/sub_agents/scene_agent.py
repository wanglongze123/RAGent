"""
Scene Agent — 场景化购物方案规划。

新版职责（与最初实现不同）：
  Step 1: 拦截 "重新规划" / "结束购物" 等控制指令，清空 scene_context
  Step 2: 用 LLM 把场景拆成 2-4 个主题（theme + query）
  Step 3: 保存 scene_context 到 DB（不立即检索、不推商品卡片）
  Step 4: 推一段文字概述 + 主题选择按钮
  后续：用户点击 "了解X" → master 路由到 search_agent 走单品流程

为什么不再直接推商品卡片：
  之前一次性推所有主题的商品，跨类目混排，用户看不出主题分组；
  且后续点击其他主题时会重复推同一批卡片，体验割裂。
  新流程让用户主动选择主题，每个主题独立走 search_agent，
  和单品流程完全统一（包括问卷、加购、对比、下单）。
"""
import json
from datetime import datetime
from typing import AsyncIterator

from app.agent.middleware import middleware
from app.db import relational as db
from app.models import events as ev


# 用户主动控制 scene 生命周期的关键词
_REPLAN_KEYWORDS = ["重新规划"]
_END_KEYWORDS = ["结束购物"]


class SceneAgent:

    async def run(
        self,
        session_id: str,
        message: str,
        params: dict,
        session: dict,
        image_base64: str | None = None,
    ) -> AsyncIterator[str]:

        # ── Step 1: 控制指令拦截（结束购物 / 重新规划）─────────
        if any(k in message for k in _END_KEYWORDS):
            await db.clear_scene_context(session_id)
            yield ev.text_delta("好的，本次场景购物已结束，期待下次为您服务～").to_sse()
            return

        if any(k in message for k in _REPLAN_KEYWORDS):
            await db.clear_scene_context(session_id)
            yield ev.text_delta(
                "好的，已清空当前方案。请重新描述您的场景需求，比如换个时间、地点、人群或预算～"
            ).to_sse()
            return

        # ── Step 2: 告知用户在规划 ─────────────────────────
        yield ev.tool_progress("scene_plan", "正在为您规划方案…").to_sse()

        # ── Step 3: LLM 拆解场景 ───────────────────────────
        plan = await self._plan_scene(message)
        if not plan or not plan.get("topics"):
            # 拆解失败：清空可能存在的旧 context，引导用户走普通搜索
            await db.clear_scene_context(session_id)
            yield ev.text_delta(
                "暂时没能识别您的场景需求，您可以换个表达，"
                "或直接告诉我想买的具体商品类目～"
            ).to_sse()
            return

        scene_summary = plan.get("scene_summary", "").strip()
        raw_topics: list[dict] = plan.get("topics", [])[:4]

        # 校验并清洗 topics：必须有 theme 和 query，theme 唯一
        topics: list[dict] = []
        seen_themes: set[str] = set()
        for t in raw_topics:
            theme = (t.get("theme") or "").strip()
            query = (t.get("query") or "").strip()
            if not theme or not query:
                continue
            if theme in seen_themes:
                continue
            seen_themes.add(theme)
            topics.append({"theme": theme, "query": query})

        if not topics:
            await db.clear_scene_context(session_id)
            yield ev.text_delta(
                "暂时没能识别出有效的主题，您可以换个表达再试试～"
            ).to_sse()
            return

        # ── Step 4: 保存 scene_context ─────────────────────
        scene_context = {
            "original_message": message,
            "scene_summary": scene_summary,
            "topics": topics,
            "created_at": datetime.utcnow().isoformat(),
        }
        await db.save_scene_context(session_id, scene_context)

        # ── Step 5: 推方案概述 ─────────────────────────────
        theme_list = "、".join(f"「{t['theme']}」" for t in topics)
        intro = (
            f"已为您规划好方案：{scene_summary}\n"
            f"包含 {len(topics)} 个主题：{theme_list}\n"
            f"请选择您想先了解的主题，我会针对该主题为您推荐商品。"
        )
        # 用 text_delta 推一次完整文本（不流式）
        yield ev.text_delta(intro).to_sse()

        # ── Step 6: 主题选择按钮 ───────────────────────────
        options = [f"了解{t['theme']}" for t in topics] + ["重新规划"]
        yield ev.clarification(
            question="您想先了解哪个主题？",
            options=options,
        ).to_sse()

    # ─────────────────────────────────────────────────────
    # 私有方法
    # ─────────────────────────────────────────────────────

    async def _plan_scene(self, message: str) -> dict | None:
        """LLM 拆解场景为主题列表，返回解析后字典或 None"""
        try:
            raw = await middleware.chat(
                agent_name="scene_planning",
                user_messages=[{"role": "user", "content": message}],
                json_mode=True,
                temperature=0.3,
            )
        except Exception as e:
            print(f"[scene_agent] _plan_scene LLM 调用失败: {e}")
            return None

        try:
            data = json.loads(raw)
            if isinstance(data, dict) and isinstance(data.get("topics"), list):
                return data
        except json.JSONDecodeError:
            pass
        return None


scene_agent = SceneAgent()
