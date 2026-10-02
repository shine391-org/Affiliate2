import { useEffect, useRef, useState } from "react"

/** Keep provider rerun intent tied to the job that opened the prompt. */
export function useJobBoundBindingPrompt(jobId) {
  const [bindingPrompt, setBindingPrompt] = useState(null)
  const activeJobId = useRef(jobId)
  activeJobId.current = jobId

  useEffect(() => {
    setBindingPrompt(null)
  }, [jobId])

  const openBindingPrompt = (action) => {
    if (activeJobId.current === jobId) setBindingPrompt({ jobId, action })
  }
  const isCurrentPrompt = (prompt) => Boolean(
    prompt && prompt.jobId === jobId && activeJobId.current === jobId
  )
  // A confirmation captured from a previous render must not act on a new job.
  const consumeBindingPrompt = () => {
    if (!isCurrentPrompt(bindingPrompt)) return null
    setBindingPrompt(null)
    return bindingPrompt.action
  }
  return { bindingPrompt, setBindingPrompt, openBindingPrompt, consumeBindingPrompt }
}
