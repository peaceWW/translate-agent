import { useEffect, useState } from 'react'
import { api, type LLMConfig } from '../api'

const defaultConfig: LLMConfig = {
  provider: 'openai',
  model: 'gpt-4o',
  api_key: '',
  base_url: 'https://api.openai.com/v1',
  max_tokens: 4096,
  system_prompt:
    '你是一位专业的学术论文翻译助手。请准确翻译学术内容，严格保留公式、数字、单位、引用编号、图表编号与专有名词。',
}

const presets = [
  { label: 'OpenAI GPT-4o', provider: 'openai', model: 'gpt-4o', base_url: 'https://api.openai.com/v1' },
  { label: 'OpenAI GPT-4.1', provider: 'openai', model: 'gpt-4.1', base_url: 'https://api.openai.com/v1' },
  { label: '通义千问 (兼容)', provider: 'qwen', model: 'qwen-plus', base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1' },
  { label: '自定义 OpenAI 兼容', provider: 'custom', model: 'custom-model', base_url: 'http://localhost:11434/v1' },
]

export default function ModelsPage() {
  const [config, setConfig] = useState<LLMConfig>(defaultConfig)
  const [message, setMessage] = useState('')
  const [ok, setOk] = useState<boolean | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    void api
      .getLLMConfig()
      .then(setConfig)
      .catch(() => { setOk(false); setMessage('模型配置加载失败，请刷新后重试') })
  }, [])

  const save = async () => {
    setBusy(true)
    setMessage('')
    try {
      const saved = await api.saveLLMConfig(config)
      setConfig(saved)
      setOk(true)
      setMessage('配置已保存')
    } catch (e) {
      setOk(false)
      setMessage(e instanceof Error ? e.message : '保存失败')
    } finally {
      setBusy(false)
    }
  }

  const test = async () => {
    setBusy(true)
    setMessage('')
    try {
      const res = await api.testLLM(config)
      setOk(res.ok)
      setMessage(res.message)
    } catch (e) {
      setOk(false)
      setMessage(e instanceof Error ? e.message : '测试失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>模型配置</h1>
          <p className="muted">支持任意 OpenAI 兼容接口：GPT / Claude 网关 / Qwen / 本地模型</p>
        </div>
      </header>

      <div className="config-card card">
        <div className="strict-note"><strong>学术严谨模式 · 固定启用</strong><p>忠实翻译，不增删论点；保留数字、公式、单位和引用。采样参数由系统固定，任务启动后锁定模型。</p></div>
        <label>快捷预设</label>
        <div className="preset-row">
          {presets.map((p) => (
            <button
              key={p.label}
              className="btn ghost"
              onClick={() =>
                setConfig((c) => ({
                  ...c,
                  provider: p.provider,
                  model: p.model,
                  base_url: p.base_url,
                }))
              }
            >
              {p.label}
            </button>
          ))}
        </div>

        <div className="form-grid">
          <label>
            Provider
            <input value={config.provider} onChange={(e) => setConfig({ ...config, provider: e.target.value })} />
          </label>
          <label>
            Model
            <input value={config.model} onChange={(e) => setConfig({ ...config, model: e.target.value })} />
          </label>
          <label className="full">
            API Base URL
            <input value={config.base_url} onChange={(e) => setConfig({ ...config, base_url: e.target.value })} />
          </label>
          <label className="full">
            API Key
            <input
              type="password"
              value={config.api_key}
              onChange={(e) => setConfig({ ...config, api_key: e.target.value })}
              placeholder="sk-..."
            />
          </label>
          <label>
            Max Tokens
            <input
              type="number"
              value={config.max_tokens}
              onChange={(e) => setConfig({ ...config, max_tokens: Number(e.target.value) })}
            />
          </label>
          <label className="full">
            补充领域说明（不覆盖学术翻译规则）
            <textarea
              rows={5}
              value={config.system_prompt}
              onChange={(e) => setConfig({ ...config, system_prompt: e.target.value })}
            />
          </label>
        </div>

        <div className="header-actions">
          <button className="btn primary" disabled={busy} onClick={() => void save()}>
            保存配置
          </button>
          <button className="btn ghost" disabled={busy} onClick={() => void test()}>
            测试连接
          </button>
        </div>

        {message && <p className={ok ? 'success' : 'error'}>{message}</p>}
      </div>
    </div>
  )
}
