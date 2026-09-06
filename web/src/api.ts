// api.ts —— 和后端 server.py 的 SSE 接口对话。
// 后端每帧是 `data: {json}\n\n`；这里用 fetch 读流、按 \n\n 切帧、JSON.parse 后回调。
// 用 fetch 而非浏览器原生 EventSource，是因为要 POST body（EventSource 只能 GET）。

export type Phase = 'researching' | 'planning' | 'teaching' | 'reviewing'

export type SSEEvent =
  | { type: 'phase'; phase: Phase }
  | { type: 'meta'; sessionId: string; outline: string[]; secIdx: number; total: number }
  | { type: 'awaiting_outline'; outline: { title: string; points: string }[] }
  | { type: 'lesson'; lesson: string; question: string; secIdx: number; total: number }
  | { type: 'section_advance'; secIdx: number; total: number; title: string }
  | { type: 'done'; draft: string; review: string }
  // 守卫动作：撞顶翻倍、空输出绕缓存重抽、格式不合重抽、降级交草稿、熔断。
  // 它们此前只 print 在服务端终端里，网页上只有一个转圈——**「空白」和「真的没事」长得一模一样**。
  | { type: 'warn'; text: string }
  | { type: 'error'; message: string }
  | { type: 'end' }

/** POST 一个 JSON body，把后端回推的 SSE 事件逐个交给 onEvent。 */
export async function streamPost(
  url: string,
  body: unknown,
  onEvent: (e: SSEEvent) => void,
): Promise<void> {
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!resp.ok || !resp.body) throw new Error(`请求失败：${resp.status}`)

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let sep: number
    while ((sep = buf.indexOf('\n\n')) !== -1) {
      const frame = buf.slice(0, sep)
      buf = buf.slice(sep + 2)
      const line = frame.split('\n').find((l) => l.startsWith('data:'))
      if (line) onEvent(JSON.parse(line.slice(5).trim()) as SSEEvent)
    }
  }
}

export const startSession = (
  topic: string,
  stub: boolean,
  onEvent: (e: SSEEvent) => void,
  confirmOutline = true,
) => streamPost('/api/start', { topic, stub, confirmOutline }, onEvent)

/** 确认提纲、开始带教。 */
export const beginSession = (
  sessionId: string,
  onEvent: (e: SSEEvent) => void,
) => streamPost('/api/begin', { sessionId }, onEvent)

/** 提纲不满意 → 重排一次。素材不重搜，只重跑策划那一次调用。
 *  feedback = 作者说的「哪里不对」，会连同【上一版提纲】一起交给策划。
 *  留空就是无方向重排——策划不知道哪儿不对，很可能交回一份差不多的。 */
export const replanOutline = (
  sessionId: string,
  feedback: string,
  onEvent: (e: SSEEvent) => void,
) => streamPost('/api/replan', { sessionId, feedback }, onEvent)

export const sendAnswer = (
  sessionId: string,
  answer: string,
  onEvent: (e: SSEEvent) => void,
) => streamPost('/api/answer', { sessionId, answer }, onEvent)

// ── 档案：把「跑过的东西」变成看得见的 ───────────────────────────────────────
// 一次真跑二十分钟，而演示窗口可能只有三分钟。产物本来就都在盘上，缺的只是入口。
export type ArchivePost = {
  slug: string
  topic: string
  mtime: number
  parts: { material: number; outline: number; transcript: number; draft: number; review: number }
}

export type ArchiveDetail = {
  slug: string
  topic: string
  parts: { material: string; outline: string; transcript: string; draft: string; review: string }
  receipt: {
    source: string
    calls: number
    by_model: Record<string, { n: number; tin: number; tout: number; cost: number; ms: number }>
    cost: number
    ms: number
    rounds: number
    author_chars: number
  }
}

export const fetchArchive = async (): Promise<ArchivePost[]> => {
  const r = await fetch('/api/archive')
  if (!r.ok) throw new Error(`档案列表读不到：HTTP ${r.status}`)
  return (await r.json()).posts ?? []
}

export const fetchArchiveOne = async (slug: string): Promise<ArchiveDetail> => {
  const r = await fetch(`/api/archive/${encodeURIComponent(slug)}`)
  if (!r.ok) throw new Error(`这一篇读不到：HTTP ${r.status}`)
  const d = await r.json()
  if (d.error) throw new Error(d.error)
  return d
}
