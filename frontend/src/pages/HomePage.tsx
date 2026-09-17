import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type DocumentMeta } from '../api'

import { statusLabel, isRunning } from '../documentStatus'

export default function HomePage() {
  const [docs, setDocs] = useState<DocumentMeta[]>([])
  const [model, setModel] = useState('加载中…')
  const [batch, setBatch] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [page, setPage] = useState(1)
  const input = useRef<HTMLInputElement>(null)
  useEffect(() => {
    let active = true
    const refresh = async () => {
      try {
        const [items, cfg] = await Promise.all([api.listDocs(), api.getPublicConfig()])
        if (active) { setDocs(items); setModel(`${cfg.provider} / ${cfg.model}`) }
      } catch (e) { if (active) setError(e instanceof Error ? e.message : '加载失败') }
    }
    void refresh()
    const timer = window.setInterval(() => void refresh(), 3000)
    return () => { active = false; clearInterval(timer) }
  }, [])
  const upload = async (files: FileList | null) => {
    if (!files?.length || busy) return
    const chosen = Array.from(files)
    if (!batch && chosen.length > 1) { setError('单文件模式一次请选择一个文件，或切换到批量翻译。'); return }
    if (chosen.some(f => !f.name.toLowerCase().endsWith('.pdf') || f.size > 100 * 1024 * 1024)) {
      setError('请选择 PDF 文件，每个文件不超过 100MB。'); return
    }
    setBusy(true); setError(''); setPage(1)
    const errors: string[] = []
    for (const file of chosen) {
      try { const doc = await api.upload(file); await api.translate(doc.doc_id) }
      catch (e) { errors.push(`${file.name}：${e instanceof Error ? e.message : '上传或启动失败'}`) }
    }
    try { setDocs(await api.listDocs()) } catch { errors.push('任务列表刷新失败，请刷新页面查看。') }
    setError(errors.join('\n')); setBusy(false)
    if (input.current) input.current.value = ''
  }
  const deleteDoc = async (docId: string, filename: string) => {
    if (!window.confirm(`确定删除「${filename}」？\n原文和译文将被永久删除，无法恢复。`)) return
    try {
      await api.deleteDoc(docId)
      setDocs(prev => prev.filter(d => d.doc_id !== docId))
    } catch (e) {
      setError(e instanceof Error ? e.message : '删除失败')
    }
  }
  const count = Math.max(1, Math.ceil(docs.length / 10))
  const current = Math.min(page, count)
  return <div className="page dashboard">
    <header className="page-header"><div><span className="eyebrow">ACADEMIC TRANSLATE</span><h1>学术文档翻译</h1><p className="muted">从上传到全文阅读，专注论文内容。</p></div><Link to="/models" className="model-pill"><span className="live-dot" />当前模型：{model}</Link></header>
    <section className={`upload-zone ${dragging ? 'dragging' : ''}`} onDragOver={e => { e.preventDefault(); setDragging(true) }} onDragLeave={() => setDragging(false)} onDrop={e => { e.preventDefault(); setDragging(false); void upload(e.dataTransfer.files) }} aria-busy={busy}>
      <div className="upload-inner">
        <div className="segmented"><button disabled={busy} className={!batch ? 'active' : ''} onClick={() => setBatch(false)}>翻译单个文件</button><button disabled={busy} className={batch ? 'active' : ''} onClick={() => setBatch(true)}>批量翻译</button></div>
        <div className="upload-symbol" aria-hidden="true">↑</div>
        <button className="btn primary upload-button" disabled={busy} onClick={() => input.current?.click()}>{busy ? '正在上传并启动翻译…' : '上传文件并翻译'}</button>
        <input ref={input} type="file" accept="application/pdf,.pdf" multiple={batch} hidden disabled={busy} onChange={e => void upload(e.target.files)} />
        <p>点击上传或将 PDF 文件拖到这里</p><p className="muted small">单个文件最大 100MB · 支持含可选择文本的 PDF · 暂不支持扫描件</p>
        <div className="upload-policy">学术严谨模式 <span>保留数字、单位、公式与引用 · 统一术语 · 自动保真校验</span></div>
      </div>
    </section>
    {error && <p className="error notice" role="alert">{error}</p>}
    <section className="task-section"><div className="section-heading"><h2>翻译任务 <span className="count">{docs.length}</span></h2><span className="muted small">任务状态自动更新</span></div>
      <div className="table-card"><table className="task-table"><thead><tr><th>文档名称</th><th>创建时间</th><th>页数</th><th>翻译模型</th><th>状态</th><th>操作</th></tr></thead><tbody>
        {docs.slice((current - 1) * 10, current * 10).map(d => <tr key={d.doc_id}>
          <td><Link className="document-name" to={`/translate/${d.doc_id}`}>{d.filename}</Link></td><td className="muted small">{new Date(d.created_at).toLocaleString('zh-CN')}</td><td>{d.page_count}</td><td><span className="model-name">{d.translation_model || '未记录'}</span><div className="muted small">{d.translation_provider || (d.status === 'uploaded' ? '启动时使用当前模型' : '历史任务')}</div></td>
          <td><span className={`status ${d.status}`}>{statusLabel[d.status] || d.status}</span>{isRunning(d.status) && <div className="task-progress"><progress max={100} value={d.progress} /><span>{Math.round(d.progress)}% · {d.completed_pages?.length || 0}/{d.page_count} 页</span></div>}<div className="small muted task-message">{d.message}</div></td>
          <td><div className="ops"><Link className="btn small" to={`/translate/${d.doc_id}`}>{d.status === 'uploaded' || d.status === 'failed' ? '打开 / 重试' : '查看'}</Link>{d.status === 'completed' && d.output_ready && <a className="btn small" href={`/api/documents/${d.doc_id}/translated.pdf`} download>下载译文</a>}<button className="btn small danger" onClick={() => void deleteDoc(d.doc_id, d.filename)}>删除</button></div></td>
        </tr>)}
        {!docs.length && <tr><td colSpan={6} className="empty-state">暂无翻译任务，上传第一篇论文开始翻译。</td></tr>}
      </tbody></table></div>
      <div className="pagination"><span className="muted">共 {docs.length} 条</span><button className="btn" disabled={current === 1} onClick={() => setPage(current - 1)}>上一页</button><span>{current} / {count}</span><button className="btn" disabled={current === count} onClick={() => setPage(current + 1)}>下一页</button></div>
    </section>
  </div>
}
