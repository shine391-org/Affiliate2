import { useEffect, useRef } from "react"
import { ws } from "../api/websocket"
import http from "../api/http"
import useAppStore from "../store/appStore"
import { localizeUiMessage } from "../utils/localizeUiMessage"

export default function useWebSocket() {
  const {
    setStreaming,
    appendStreamToken,
    finalizeStream,
    clearStreamingText,
    setActiveWizard,
    addMessage,
    prependSession,
    updateSession,
    setActiveSessionId,
    showSnackbar,
    startJob,
    updateJobProgress,
    completeJob,
    failJob,
    removeJob,
  } = useAppStore()

  // Chat tokens arrive far faster than the screen refreshes. Writing each one
  // straight to the store cost a store notify + a markdown re-parse of the
  // WHOLE partial response per token — quadratic over a long answer, and the
  // main thread felt it. Buffer instead and flush at most once per frame; the
  // rendered result is identical because appendStreamToken is a string concat.
  const tokenBufRef = useRef("")
  const rafRef = useRef(null)
  const streamOpenRef = useRef(false)

  useEffect(() => {
    ws.connect()
    let mounted = true

    // Restore running/pending jobs from API so active jobs survive page refresh
    const restoreJobs = async () => {
      try {
        // One status filter, fetched in pages: a long-running job must survive
        // refresh even when it is older than the first 40 active rows.
        let offset = 0
        let total = 0
        do {
          const { data } = await http.get("/api/jobs", {
            params: { status: "running,pending,queued", limit: 100, offset },
          })
          if (!mounted) return
          const page = data.jobs || []
          total = Number(data.total || 0)
          if (!page.length) break
          offset += page.length
          page.forEach(j => {
          if (!useAppStore.getState().activeJobs[j.id]) {
            const msg = localizeUiMessage(j.current_step || j.title || j.job_type)
            // Parse input_json to preserve job metadata (e.g. type: "clip_extraction")
            let inputData = null
            try { inputData = j.input_json ? JSON.parse(j.input_json) : null } catch {}
            // Restore job into global store
            startJob(j.id, j.job_type, msg, { inputData })
            if (j.progress_pct > 0) {
              updateJobProgress(j.id, j.progress_pct, j.current_step || "")
            }
          }
          })
        } while (offset < total)
      } catch (err) {
        // Silent fail — jobs will be picked up on next WS message
      }
    }
    restoreJobs()

    // Drain the token buffer into the store. Safe to call at any time; a no-op
    // when empty. MUST be called synchronously before any handler that reads
    // streamingText (finalizeStream falls back to it when full_response is
    // missing) or clears it (chat_error) — otherwise a frame's worth of
    // buffered tokens is dropped, or a pending frame writes text back AFTER the
    // clear.
    const flushTokens = () => {
      if (rafRef.current != null) {
        cancelAnimationFrame(rafRef.current)
        rafRef.current = null
      }
      const buf = tokenBufRef.current
      if (!buf) return
      tokenBufRef.current = ""
      appendStreamToken(buf)
    }

    const endStream = () => {
      flushTokens()
      streamOpenRef.current = false
    }

    const unsubs = [
      ws.on("chat_token", (msg) => {
        // Flip the composer lock once per turn, not once per token.
        if (!streamOpenRef.current) {
          streamOpenRef.current = true
          setStreaming(true)
        }
        tokenBufRef.current += msg.token
        if (rafRef.current == null) {
          rafRef.current = requestAnimationFrame(() => {
            rafRef.current = null
            const buf = tokenBufRef.current
            if (!buf) return
            tokenBufRef.current = ""
            appendStreamToken(buf)
          })
        }
      }),

      ws.on("chat_done", (msg) => {
        endStream()  // land buffered tokens before finalizeStream reads them
        // quick_replies is an optional list emitted when the AI included a
        // <quick_replies> block. Bundling it onto chat_done keeps the chips
        // attached atomically to the message that just finished — no race
        // where the bubble renders before its chips arrive.
        finalizeStream(msg.full_response, msg.quick_replies)
      }),

      // Dispatch-time follow-up from the planner (a clarifying question, a
      // no-op note) — sent AFTER chat_done. Render it as a normal assistant
      // bubble with optional quick-reply chips. Crucially it does NOT call
      // setStreaming: the old path emitted these as chat_token, which flipped
      // isStreaming=true with no matching chat_done and locked the composer
      // forever ("which platform?" → user can't answer). Not persisted here —
      // the backend folds the text into the turn's assistant message.
      ws.on("assistant_message", (msg) => {
        const content = (msg.content || "").trim()
        if (!content) return
        const m = { role: "assistant", content }
        if (Array.isArray(msg.quick_replies) && msg.quick_replies.length > 0) {
          m.quickReplies = msg.quick_replies
        }
        addMessage(m)
      }),

      ws.on("chat_error", (msg) => {
        endStream()
        setStreaming(false)
        clearStreamingText()
        addMessage({ role: "system", content: `Lỗi: ${localizeUiMessage(msg.error)}` })
      }),

      ws.on("smart_suggestions", () => {
        // Suggestions only shown on new chat screen, not mid-conversation
      }),

      ws.on("wizard_start", (msg) => {
        setActiveWizard({ id: msg.wizard_id, ...msg.wizard })
      }),

      ws.on("wizard_step_result", () => {
        // Handled by SetupWizard component directly
      }),

      // Session management
      ws.on("session_created", (msg) => {
        const newSession = {
          id: msg.session_id,
          title: msg.title,
          message_count: 0,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        }
        prependSession(newSession)
        setActiveSessionId(msg.session_id)
      }),

      ws.on("session_updated", (msg) => {
        updateSession(msg.session_id, {
          title: msg.title,
          message_count: msg.message_count,
          updated_at: new Date().toISOString(),
        })
      }),

      ws.on("job_started", (msg) => {
        startJob(msg.job_id, msg.job_type, localizeUiMessage(msg.message), { inputData: msg.input_data || null })
        addMessage({
          role: "rich",
          type: "job_progress",
          data: { jobId: msg.job_id, jobType: msg.job_type, message: localizeUiMessage(msg.message) },
        })
      }),

      ws.on("job_progress", (msg) => {
        updateJobProgress(msg.job_id, msg.percent, localizeUiMessage(msg.step))
      }),

      ws.on("job_attention", (msg) => {
        // Human review is an intentional pause, not a completed job and not
        // work still consuming CPU. Remove it from the active-job badge; the
        // Activity poll will surface it under "Cần thao tác".
        removeJob(msg.job_id)
        const href = msg.action_url || `/studio/localize?job=${encodeURIComponent(msg.job_id)}&step=translation`
        const message = localizeUiMessage(
          msg.message || "Bản dịch đã xong — cần duyệt trước khi tiếp tục.",
        )
        addMessage({ role: "system", content: message })
        showSnackbar(message, "warning", { label: "Mở bản dịch", href })
      }),

      ws.on("job_complete", (msg) => {
        completeJob(msg.job_id)
        // Auto-remove from sidebar after 10s
        setTimeout(() => removeJob(msg.job_id), 10000)
        const result = msg.result || {}
        // Determine job type from active jobs store
        const jobInfo = useAppStore.getState().activeJobs[msg.job_id]
        const jobType = jobInfo?.jobType || msg.job_type || ""
        const qcStatus = String(result.qc_status || "").toUpperCase()
        if (jobType === "tool:translate" && ["NEEDS_REVIEW", "FAILED"].includes(qcStatus)) {
          const failed = qcStatus === "FAILED"
          showSnackbar(
            failed
              ? "Video đã kết xuất nhưng QC phát hiện lỗi — cần xử lý trước khi dùng tiếp."
              : "Video đã kết xuất nhưng QC cần bạn duyệt.",
            failed ? "error" : "warning",
            { label: "Mở Duyệt QC", href: `/review?job=${encodeURIComponent(msg.job_id)}` },
          )
        }

        if (result.total_results !== undefined) {
          const total = result.total_results
          const newCount = result.new_results
          let text
          if (jobType === "news_scout") {
            text = total > 0
              ? `Nghiên cứu tin tức hoàn tất — tìm thấy **${total}** bài viết.`
              : "Nghiên cứu tin tức hoàn tất — chưa có bài viết đủ điểm. Hãy thử truy vấn khác hoặc cụ thể hơn."
          } else {
            text = newCount !== undefined && newCount < total
              ? `Quét hoàn tất — tìm thấy **${total}** video (${newCount} mới, ${total - newCount} đã quét trước đó).`
              : `Quét hoàn tất — tìm thấy **${total}** video đang thịnh hành.`
          }
          addMessage({ role: "system", content: text })
        } else if (result.video) {
          addMessage({
            role: "rich",
            type: "video_preview",
            data: { video: result.video },
          })
        } else if (result.insights) {
          addMessage({
            role: "rich",
            type: "insights",
            data: { videos: result.insights },
          })
        } else {
          addMessage({ role: "system", content: "Tác vụ hoàn tất!" })
        }
      }),

      ws.on("job_failed", (msg) => {
        failJob(msg.job_id, msg.error)
        addMessage({
          role: "system",
          content: `Tác vụ thất bại: ${localizeUiMessage(msg.error || "Lỗi không xác định")}`,
        })
        showSnackbar(localizeUiMessage(msg.error || "Một tác vụ thất bại — hãy xem chi tiết trong trò chuyện"), "error")
      }),

      ws.on("channel_analysis", (msg) => {
        addMessage({
          role: "rich",
          type: "channel_summary",
          data: { summary: msg.summary },
        })
      }),

      ws.on("content_calendar", (msg) => {
        addMessage({
          role: "rich",
          type: "content_calendar",
          data: { calendar: msg.calendar || [] },
        })
      }),

      ws.on("downloaded_list", (msg) => {
        addMessage({
          role: "rich",
          type: "downloaded_list",
          data: { videos: msg.videos || [] },
        })
      }),

      ws.on("news_results", (msg) => {
        addMessage({
          role: "rich",
          type: "news_results",
          data: { results: msg.results, query: msg.query, jobId: msg.job_id },
        })
      }),

      ws.on("news_saved", (msg) => {
        addMessage({
          role: "system",
          content: localizeUiMessage(msg.message) || `${msg.count} bài viết đã lưu vào Thư viện.`,
        })
      }),

      ws.on("scout_results", (msg) => {
        const store = useAppStore.getState()
        const existing = store.scoutResults || []
        const newResults = msg.results || []
        useAppStore.setState({ scoutResults: [...existing, ...newResults] })

        addMessage({
          role: "rich",
          type: "scout_results",
          data: { results: msg.results, platform: msg.platform, jobId: msg.job_id },
        })
      }),

      ws.on("constraint_warning", (msg) => {
        addMessage({
          role: "system",
          content: `${localizeUiMessage(msg.message)}`,
          severity: msg.severity,
          wizardId: msg.wizard_id,
        })
        showSnackbar(localizeUiMessage(msg.message), msg.severity === "error" ? "error" : "warning")
      }),

      ws.on("morning_digest", (msg) => {
        addMessage({ role: "assistant", content: msg.message })
      }),

      ws.on("action", () => {
        // Actions dispatched by planner backend
      }),
    ]

    return () => {
      mounted = false
      unsubs.forEach(fn => fn())
      if (rafRef.current != null) {
        cancelAnimationFrame(rafRef.current)
        rafRef.current = null
      }
      tokenBufRef.current = ""
      streamOpenRef.current = false
    }
  }, [])
}
