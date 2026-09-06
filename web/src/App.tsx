// App.tsx —— 写作助手前端主体。
// 三个阶段：input（选题）→ running（逐节「讲→问→你答」的对话）→ done（初稿 + 终审）。
// 后端用 SSE 推「进度 phase / 一轮 lesson / 换节 / 收尾 done」；这里收到后渲染。
// 过渡用 framer-motion 的 AnimatePresence(mode="wait")：旧页先淡出、新页再进 → 不会两页重叠残留。

import { useEffect, useRef, useState } from 'react'
import type { KeyboardEvent, ReactNode } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { sendAnswer, startSession, beginSession, replanOutline, fetchArchive, fetchArchiveOne } from './api'
import type { ArchivePost, ArchiveDetail } from './api'
import type { Phase, SSEEvent } from './api'

// ── 一节里已完成的一轮（讲/问/你答），或换节分隔 ────────────────────────────
type Item =
  | { kind: 'turn'; lesson: string; q: string; a: string }
  | { kind: 'section'; title: string }

const PHASE_TEXT: Record<Phase, string> = {
  researching: '研究员查证中…',
  planning: '策划分节中…',
  teaching: '导师备课中…',
  reviewing: '终审把关中…',
}

const TYPING_MS = 36 // 每字毫秒；想快/慢就改这一处

// ── 打字机：把已拿到的整段逐字打出来；点一下可瞬间显示全文（跳过）──────────────
function Typewriter({ text, onDone }: { text: string; onDone?: () => void }) {
  const [n, setN] = useState(0)
  const done = useRef(false)
  const onDoneRef = useRef(onDone)
  onDoneRef.current = onDone

  useEffect(() => {
    setN(0)
    done.current = false
  }, [text])

  useEffect(() => {
    if (n >= text.length) {
      if (!done.current) {
        done.current = true
        onDoneRef.current?.()
      }
      return
    }
    const id = setTimeout(() => setN((x) => x + 1), TYPING_MS)
    return () => clearTimeout(id)
  }, [n, text])

  return (
    <div className="lesson" style={{ whiteSpace: 'pre-wrap' }} onClick={() => setN(text.length)}>
      {text.slice(0, n)}
      {n < text.length && <span className="caret" />}
    </div>
  )
}

// ── 极简 markdown：成稿/终审页够用（标题 / 段落 / 列表 / 引用 / **粗体**）──────────
function inline(text: string): ReactNode {
  return text
    .split(/(\*\*[^*]+\*\*)/g)
    .map((p, i) =>
      p.startsWith('**') && p.endsWith('**') ? <strong key={i}>{p.slice(2, -2)}</strong> : <span key={i}>{p}</span>,
    )
}

function Markdown({ text, className }: { text: string; className?: string }) {
  const blocks: ReactNode[] = []
  let para: string[] = []
  let list: string[] = []
  const flushPara = () => {
    if (para.length) blocks.push(<p key={blocks.length}>{inline(para.join(' '))}</p>)
    para = []
  }
  const flushList = () => {
    if (list.length)
      blocks.push(
        <ul key={blocks.length}>
          {list.map((li, i) => (
            <li key={i}>{inline(li)}</li>
          ))}
        </ul>,
      )
    list = []
  }
  for (const raw of text.split('\n')) {
    const line = raw.trimEnd()
    if (!line.trim()) {
      flushPara()
      flushList()
    } else if (line.startsWith('### ')) {
      flushPara(); flushList(); blocks.push(<h3 key={blocks.length}>{inline(line.slice(4))}</h3>)
    } else if (line.startsWith('## ')) {
      flushPara(); flushList(); blocks.push(<h2 key={blocks.length}>{inline(line.slice(3))}</h2>)
    } else if (line.startsWith('# ')) {
      flushPara(); flushList(); blocks.push(<h1 key={blocks.length}>{inline(line.slice(2))}</h1>)
    } else if (line.startsWith('> ')) {
      flushPara(); flushList(); blocks.push(<blockquote key={blocks.length}>{inline(line.slice(2))}</blockquote>)
    } else if (/^[-*]\s/.test(line)) {
      flushPara(); list.push(line.replace(/^[-*]\s/, ''))
    } else {
      flushList(); para.push(line.trim())
    }
  }
  flushPara()
  flushList()
  return <div className={className}>{blocks}</div>
}

const PenMark = () => (
  <svg width="52" height="52" viewBox="0 0 64 64" fill="none" aria-hidden>
    <g transform="translate(13 51) rotate(-35)">
      <rect x="40" y="-5" width="20" height="10" rx="5" fill="#a8472a" />
      <rect x="16" y="-4.5" width="26" height="9" rx="4.5" fill="#c15f3c" />
      <rect x="40" y="-5" width="3" height="10" fill="#d4a27f" />
      <path d="M0 0 L 14 -4.5 L 14 4.5 Z" fill="#d4a27f" stroke="#a8472a" strokeWidth="0.9" strokeLinejoin="round" />
      <line x1="3" y1="0" x2="12" y2="0" stroke="#a8472a" strokeWidth="0.9" />
    </g>
    <circle cx="9" cy="56" r="1.8" fill="#c15f3c" />
    <path d="M22 53 q 9 -3 18 0" stroke="#a8472a" strokeWidth="1.5" opacity="0.5" fill="none" />
  </svg>
)

// 导师头像：戴学士帽的小人（陶土三色，和封面钢笔徽标同一套矢量风格）
const TutorAvatar = () => (
  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden>
    <path d="M4.7 21.5 C 4.7 17, 19.3 17, 19.3 21.5 Z" fill="#c15f3c" />
    <circle cx="12" cy="13" r="3.3" fill="#c15f3c" />
    <path d="M12 5.2 L 19.6 8 L 12 10.8 L 4.4 8 Z" fill="#a8472a" />
    <circle cx="12" cy="8" r="0.95" fill="#d4a27f" />
    <path d="M16 9.3 L 16.5 12.4" stroke="#d4a27f" strokeWidth="0.9" strokeLinecap="round" />
    <circle cx="16.6" cy="12.9" r="0.95" fill="#d4a27f" />
  </svg>
)

// 已完成的一轮：导师消息（讲+问）+ 你靠右的回答
function TurnView({ lesson, q, a }: { lesson: string; q: string; a: string }) {
  return (
    <>
      <div className="msg-tutor">
        <div className="avatar"><TutorAvatar /></div>
        <div className="tutor-body">
          <div className="lesson" style={{ whiteSpace: 'pre-wrap' }}>
            {lesson}
          </div>
          <div className="question">{q}</div>
        </div>
      </div>
      <div className="msg-user">{a}</div>
    </>
  )
}

const stageMotion = {
  initial: { opacity: 0, y: 10 },
  animate: { opacity: 1, y: 0 },
  exit: { opacity: 0, y: -8 },
  transition: { duration: 0.32, ease: 'easeOut' as const },
}

const TAB_LABEL = {
  material: '素材', outline: '提纲', transcript: '逐句问答', draft: '初稿', review: '终审',
} as const

const kb = (n: number) => (n >= 1024 ? `${(n / 1024).toFixed(1)}k` : `${n}`)

/** 制作凭据：这一篇在自建网关账本上留下的每一笔。
 *  账本查不到时**如实说查不到**，绝不拿全局总量顶上——
 *  和这个项目里「没有存档 ≠ 那天没东西」是同一条纪律。 */
function Receipt({ r }: { r: ArchiveDetail['receipt'] }) {
  const models = Object.entries(r.by_model)
  return (
    <div className="receipt">
      <div className="receipt-hd">制作凭据 · <code>{r.source}</code></div>
      {r.rounds > 0 && (
        <div className="receipt-line">
          作者本人打了 <b>{r.author_chars}</b> 字，分 <b>{r.rounds}</b> 轮 ——
          逐句原话在「逐句问答」那一栏，一个字没改过
        </div>
      )}
      {r.calls > 0 ? (
        <>
          <div className="receipt-line">
            共 <b>{r.calls}</b> 次模型调用 ｜ ${r.cost.toFixed(4)} ｜ {(r.ms / 1000).toFixed(1)}s
          </div>
          {models.map(([m, d]) => (
            <div key={m} className="receipt-line dim">
              · {m} {d.n} 次 ｜ in {d.tin} / out {d.tout} tok ｜ ${d.cost.toFixed(4)}
            </div>
          ))}
        </>
      ) : (
        <div className="receipt-line dim">
          账本里查不到这一篇 —— 它是加按篇来源戳之前跑的，流量混在「未标注」里分不出来。
          <b>查不到就是查不到，这里不拿全局总量替它编一个数。</b>
        </div>
      )}
    </div>
  )
}

export default function App() {
  const [stage, setStage] = useState<'input' | 'archive' | 'running' | 'done'>('input')
  const [stub, setStub] = useState(true)
  const [topic, setTopic] = useState('')

  const [sessionId, setSessionId] = useState('')
  const [outline, setOutline] = useState<string[]>([])
  // 提纲确认闸：提纲出来先停住，人看过再开跑。提纲是整条链的地基，
  // 而这里是最便宜的干预点——一次调用之后，一道题都还没答。
  const [pendingOutline, setPendingOutline] = useState<{ title: string; points: string }[] | null>(null)
  // 闸口上作者写的「哪里不对」。空＝无方向重排（只是重新掷一次骰子）。
  const [outlineNote, setOutlineNote] = useState('')
  // 守卫动作。它们此前只在服务端终端里，界面上只有一个转圈——
  // 而**「它坏过又自己恢复了」恰恰是这套东西最不像玩具的地方，却是唯一看不见的地方**。
  const [warns, setWarns] = useState<string[]>([])
  const [warnsOpen, setWarnsOpen] = useState(false)
  // 档案：跑过的每一篇 + 它留下的产物。第一屏就该有东西可看，而不是一个空输入框。
  const [archive, setArchive] = useState<ArchivePost[]>([])
  const [viewing, setViewing] = useState<ArchiveDetail | null>(null)
  const [viewTab, setViewTab] = useState<keyof ArchiveDetail['parts']>('draft')
  const [archiveErr, setArchiveErr] = useState('')
  const [secIdx, setSecIdx] = useState(0)
  const [total, setTotal] = useState(0)

  const [items, setItems] = useState<Item[]>([])
  const [current, setCurrent] = useState<{ lesson: string; question: string } | null>(null)
  const [typingDone, setTypingDone] = useState(false)
  const [phase, setPhase] = useState<Phase | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [answer, setAnswer] = useState('')
  const [draft, setDraft] = useState('')
  const [review, setReview] = useState('')

  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    // 读不到不算错误——第一次用的人本来就没有档案。**「还没有」和「坏了」是两件事。**
    fetchArchive().then(setArchive).catch((e) => setArchiveErr(String(e)))
  }, [])

  async function openPost(slug: string) {
    setArchiveErr('')
    try {
      const d = await fetchArchiveOne(slug)
      setViewing(d)
      setViewTab(d.parts.draft ? 'draft' : 'material')
    } catch (e) {
      setArchiveErr(String(e))
    }
  }
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [items, current, typingDone, phase])

  function handle(e: SSEEvent) {
    switch (e.type) {
      case 'phase':
        setPhase(e.phase)
        break
      case 'meta':
        setSessionId(e.sessionId)
        setOutline(e.outline)
        setSecIdx(e.secIdx)
        setTotal(e.total)
        break
      case 'awaiting_outline':
        setPhase(null)
        setPendingOutline(e.outline)
        break
      case 'lesson':
        setPhase(null)
        setPendingOutline(null)
        setSecIdx(e.secIdx)
        setTotal(e.total)
        setTypingDone(false)
        setCurrent({ lesson: e.lesson, question: e.question })
        break
      case 'section_advance':
        setItems((prev) => [...prev, { kind: 'section', title: e.title }])
        setSecIdx(e.secIdx)
        setTotal(e.total)
        break
      case 'done':
        setPhase(null)
        setDraft(e.draft)
        setReview(e.review)
        setStage('done')
        break
      case 'warn':
        setWarns((prev) => [...prev, e.text])
        break
      case 'error':
        setError(e.message)
        setBusy(false)
        break
      case 'end':
        setBusy(false)
        break
    }
  }

  async function start() {
    const t = topic.trim()
    if (!t || busy) return
    setStage('running')
    setItems([])
    setCurrent(null)
    setError(null)
    setWarns([])
    setBusy(true)
    setPhase('researching')
    try {
      await startSession(t, stub, handle)
    } catch (err) {
      setError(String(err))
      setBusy(false)
    }
  }

  async function confirmOutline() {
    if (busy) return
    setPendingOutline(null)
    setBusy(true)
    setPhase('teaching')
    try {
      await beginSession(sessionId, handle)
    } catch (err) {
      setError(String(err))
      setBusy(false)
    }
  }

  async function replan() {
    if (busy) return
    setBusy(true)
    setPhase('planning')
    const note = outlineNote.trim()
    try {
      await replanOutline(sessionId, note, handle)
      setOutlineNote('')   // 已经带过去了，别让它留在框里污染下一次重排
    } catch (err) {
      setError(String(err))
      setBusy(false)
    }
  }

  async function submit() {
    const a = answer.trim()
    if (!a || !current || !typingDone || busy) return
    setItems((prev) => [...prev, { kind: 'turn', lesson: current.lesson, q: current.question, a }])
    setCurrent(null)
    setAnswer('')
    setBusy(true)
    setPhase('teaching')
    try {
      await sendAnswer(sessionId, a, handle)
    } catch (err) {
      setError(String(err))
      setBusy(false)
    }
  }

  function reset() {
    setStage('input')
    setItems([])
    setCurrent(null)
    setOutline([])
    setPendingOutline(null)
    setSecIdx(0)
    setTotal(0)
    setDraft('')
    setReview('')
    setError(null)
    setBusy(false)
    setPhase(null)
    setTopic('')
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  }

  function download() {
    const url = URL.createObjectURL(new Blob([draft], { type: 'text/markdown' }))
    const a = document.createElement('a')
    a.href = url
    a.download = 'draft.md'
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="app">
      <div className="topbar">
        <span className="brand">写作助手</span>
        {stage === 'input' ? (
          <span
            className={'stub-toggle' + (stub ? '' : ' real')}
            onClick={() => setStub((s) => !s)}
            title="开＝写死的假内容点通流程，不花钱；关＝真调 agent（产生费用）"
          >
            <span className="dot" />
            {stub ? '假数据模式 · 免费' : '真实模式 · 会计费'}
          </span>
        ) : (
          <span className="stub-toggle" style={{ cursor: 'default' }}>
            <span className="dot" style={{ background: stub ? '#6b8f71' : 'var(--accent)' }} />
            {stub ? '假数据' : '真实'}
          </span>
        )}
      </div>

      <AnimatePresence mode="wait">
        {stage === 'input' && (
          <motion.div key="input" {...stageMotion}>
            <div className="hero">
              <img className="hero-img" src="/pen.jpeg" alt="写作助手" />
              <h1 className="hero-title">写作助手</h1>
              <p className="hero-sub">像导师一样先讲再问，带你边学边写 —— 产出你自己声音的初稿与终审意见。</p>
              <div className="topic-form">
                <input
                  className="topic-input"
                  placeholder="想写什么？输入一个选题…"
                  value={topic}
                  onChange={(e) => setTopic(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && start()}
                />
                <button className="btn btn-primary" onClick={start} disabled={!topic.trim()}>
                  开始写作
                </button>
              </div>
            </div>

            {archive.length > 0 && (
              <button className="archive-entry" onClick={() => { setViewing(null); setStage('archive') }}>
                看看写过的 {archive.length} 篇
                <span className="archive-entry-sub">从素材到终审，每一步都留着</span>
              </button>
            )}
          </motion.div>
        )}

        {stage === 'archive' && (
          <motion.div key="archive" {...stageMotion}>
            {/* 独立一页。一次真跑二十分钟，而演示窗口可能只有三分钟——
                **所以要让已经跑过的东西看得见**，而不是让人等。 */}
            <div className="arc-head">
              <button className="btn btn-quiet" onClick={() => (viewing ? setViewing(null) : setStage('input'))}>
                ← {viewing ? '写过的' : '返回'}
              </button>
              <div className="arc-head-t">{viewing ? viewing.topic : '写过的'}</div>
            </div>

            {!viewing && (
              <>
                <p className="arc-lede">
                  {archive.length} 篇 —— 每一篇都留着从<b>素材</b>、<b>提纲</b>、
                  <b>你逐句答的原话</b>到<b>初稿</b>和<b>终审意见</b>的全过程。
                </p>
                <div className="arc-list">
                  {archive.map((post) => (
                    <button key={post.slug} className="arc-item" onClick={() => openPost(post.slug)}>
                      <span className="arc-title">{post.topic}</span>
                      <span className="arc-meta">
                        {(['material', 'outline', 'transcript', 'draft', 'review'] as const).map((k) => (
                          <span key={k} className={'arc-chip' + (post.parts[k] ? '' : ' none')}>
                            {TAB_LABEL[k]}{post.parts[k] ? <b>{kb(post.parts[k])}</b> : null}
                          </span>
                        ))}
                      </span>
                    </button>
                  ))}
                </div>
              </>
            )}

            {viewing && (
              <>
                <Receipt r={viewing.receipt} />
                <div className="arc-tabs">
                  {(['material', 'outline', 'transcript', 'draft', 'review'] as const).map((k) => (
                    <button
                      key={k}
                      className={'arc-tab' + (viewTab === k ? ' on' : '')}
                      onClick={() => setViewTab(k)}
                      disabled={!viewing.parts[k]}
                      title={viewing.parts[k] ? '' : '这一样没有留下'}
                    >
                      {TAB_LABEL[k]}
                    </button>
                  ))}
                </div>
                {!viewing.parts[viewTab] ? (
                  <div className="arc-empty">这一样没有留下。</div>
                ) : viewTab === 'transcript' || viewTab === 'outline' ? (
                  // 逐句问答和提纲是**记录**不是文章：等宽、原样，别渲染掉它的形状
                  <pre className="arc-raw">{viewing.parts[viewTab]}</pre>
                ) : (
                  <Markdown className="doc arc-doc" text={viewing.parts[viewTab]} />
                )}
              </>
            )}
            {archiveErr && <div className="arc-err">读不到：{archiveErr}</div>}
          </motion.div>
        )}

        {stage === 'running' && (
          <motion.div key="running" {...stageMotion}>
            {total > 0 && (
              <div className="progress">
                <div className="progress-label">
                  第 {Math.min(secIdx + 1, total)} / {total} 节{outline[secIdx] ? ` · ${outline[secIdx]}` : ''}
                </div>
                <div className="progress-track">
                  <div className="progress-fill" style={{ width: `${(secIdx / total) * 100}%` }} />
                </div>
              </div>
            )}
            {/* 守卫条。这条链上所有守卫——撞顶翻倍、空输出绕缓存重抽、格式不合重抽、
                降级交草稿、轮数熔断——此前动作全在服务端终端里，页面上只有一个转圈。
                **「它坏过、又自己恢复了」恰恰是这套东西最不像玩具的地方，却是唯一看不见的。** */}
            {warns.length > 0 && (
              <div className={'guards' + (warnsOpen ? ' open' : '')}>
                <button className="guards-hd" onClick={() => setWarnsOpen((v) => !v)}>
                  <span className="guards-dot" />
                  守卫动作 {warns.length} 次 —— 它自己处理掉了
                  <span className="guards-caret">{warnsOpen ? '收起' : '展开'}</span>
                </button>
                {warnsOpen && (
                  <ul className="guards-list">
                    {warns.map((w, i) => <li key={i}>{w}</li>)}
                  </ul>
                )}
              </div>
            )}

            {/* 提纲确认闸：提纲是整条链的地基，歪了后面全歪，而这里是最便宜的干预点 */}
            {pendingOutline && (
              <div className="outline-gate">
                <div className="outline-gate-hd">
                  策划排了 {pendingOutline.length} 节 —— 先看一眼，跑偏了现在改最便宜
                </div>
                <ol className="outline-list">
                  {pendingOutline.map((sec, i) => (
                    <li key={i}>
                      <b>{sec.title}</b>
                      {sec.points ? <span className="outline-points">{sec.points}</span> : null}
                    </li>
                  ))}
                </ol>
                <textarea
                  className="outline-note"
                  value={outlineNote}
                  onChange={(e) => setOutlineNote(e.target.value)}
                  placeholder="哪里不对？用你自己的话写一句 —— 例如「第 2 节太抽象，请换成素材中的具体例子」"
                  rows={2}
                  disabled={busy}
                />
                <div className="outline-actions">
                  <button className="btn btn-primary" onClick={confirmOutline} disabled={busy}>
                    就按这个开始
                  </button>
                  <button className="btn" onClick={replan} disabled={busy}>
                    {outlineNote.trim() ? '按意见重排' : '重排一次'}
                  </button>
                </div>
                <div className="outline-hint">
                  重排只重跑「策划」那一次调用，<b>不会重新上网搜素材</b>（那是两分钟真金白银）。
                  上面那句话会连同<b>上一版提纲</b>一起交给策划——不写就只是重新掷一次骰子。
                </div>
              </div>
            )}

            {/* 带教途中：整份提纲随时可展开，当前节高亮——「有没有跑偏」一眼看得见 */}
            {!pendingOutline && outline.length > 0 && (
              <details className="outline-peek">
                <summary>全文提纲（{outline.length} 节）</summary>
                <ol className="outline-list">
                  {outline.map((t, i) => (
                    <li key={i} className={i === secIdx ? 'now' : i < secIdx ? 'done' : ''}>
                      {t}
                    </li>
                  ))}
                </ol>
              </details>
            )}

            {error && <div className="err">{error}</div>}
            <div className="transcript">
              {items.map((it, i) =>
                it.kind === 'section' ? (
                  <div className="section-divider" key={i}>
                    {it.title}
                  </div>
                ) : (
                  <TurnView key={i} lesson={it.lesson} q={it.q} a={it.a} />
                ),
              )}
              {current && (
                <div className="msg-tutor">
                  <div className="avatar"><TutorAvatar /></div>
                  <div className="tutor-body">
                    <Typewriter text={current.lesson} onDone={() => setTypingDone(true)} />
                    {typingDone && (
                      <motion.div className="question" initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }}>
                        {current.question}
                      </motion.div>
                    )}
                  </div>
                </div>
              )}
              {phase && (
                <div className="phase-line">
                  <span className="pulse" />
                  {PHASE_TEXT[phase]}
                </div>
              )}
              <div ref={bottomRef} />
            </div>
          </motion.div>
        )}

        {stage === 'done' && (
          <motion.div key="done" {...stageMotion}>
            <div className="paper">
              <div className="done-mark">
                <PenMark />
              </div>
              {/* 交付块排在正文【前面】：原来一上来就是六千字，看的人翻半天才知道
                  「这东西存哪了、我自己贡献了多少」。**先说交付了什么，再给正文。** */}
              <div className="delivery">
                <div className="delivery-row">
                  <b>{total}</b> 节访谈完毕 ｜ 初稿 <b>{draft.length}</b> 字
                  {warns.length > 0 && <> ｜ 途中守卫接管 <b>{warns.length}</b> 次</>}
                </div>
                <div className="delivery-row dim">
                  你本人答了 <b>{items.filter((x) => x.kind === 'turn').length}</b> 轮
                  —— 逐句原话已落盘，编辑拿到的是你的话，不是模型的自由发挥
                </div>
              </div>
              <div className="eyebrow">初稿 · 由你的回答写成</div>
              <Markdown className="doc" text={draft} />
              <hr className="divider" />
              <div className="eyebrow">终审意见 · 建议，改不改你定</div>
              <Markdown className="review" text={review} />
              <div className="done-actions">
                <button className="btn btn-primary" onClick={download}>
                  下载初稿 .md
                </button>
                <button className="btn" onClick={reset}>
                  再写一篇
                </button>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {stage === 'running' && (
        <div className="composer">
          <div className="composer-inner">
            <textarea
              rows={1}
              value={answer}
              onChange={(e) => setAnswer(e.target.value)}
              onKeyDown={onKey}
              placeholder={busy ? '导师在思考…' : current && typingDone ? '回复导师…（Enter 发送）' : '…'}
              disabled={busy || !current || !typingDone}
            />
            <button
              className="btn btn-primary"
              onClick={submit}
              disabled={busy || !answer.trim() || !current || !typingDone}
            >
              发送
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
