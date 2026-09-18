import { useEffect, useState } from 'react'
import { api, type LLMConfig } from '../api'

const defaultConfig: LLMConfig = {
  provider: 'deepseek',
  model: 'deepseek-chat',
  api_key: '',
  base_url: 'https://api.deepseek.com/v1',
  max_tokens: 4096,
  system_prompt:
    '你是一位专业的学术论文翻译助手。请准确翻译学术内容，严格保留公式、数字、单位、引用编号、图表编号与专有名词。',
  translation_model: '',
  translation_base_url: '',
  translation_api_key: '',
  vision_model: '',
  vision_base_url: '',
  vision_api_key: '',
  vision_scale: 2,
}

const presets = [
  { label: 'DeepSeek', provider: 'deepseek', model: 'deepseek-chat', base_url: 'https://api.deepseek.com/v1' },
  { label: '智谱 GLM-4', provider: 'zhipu', model: 'glm-4-plus', base_url: 'https://open.bigmodel.cn/api/paas/v4' },
  { label: 'Kimi (Moonshot)', provider: 'moonshot', model: 'moonshot-v1-8k', base_url: 'https://api.moonshot.cn/v1' },
  { label: '通义千问', provider: 'qwen', model: 'qwen-plus', base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1' },
  { label: '自定义 OpenAI 兼容', provider: 'custom', model: 'custom-model', base_url: 'http://localhost:11434/v1' },
]

const translationPresets = [
  { label: '与推理模型相同', translation_model: '', translation_base_url: '' },
  { label: '通义千问 MT', translation_model: 'qwen-mt-plus', translation_base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1' },
  { label: 'DeepSeek', translation_model: 'deepseek-chat', translation_base_url: 'https://api.deepseek.com/v1' },
  { label: '智谱 GLM-4', translation_model: 'glm-4-plus', translation_base_url: 'https://open.bigmodel.cn/api/paas/v4' },
  { label: 'Kimi', translation_model: 'moonshot-v1-8k', translation_base_url: 'https://api.moonshot.cn/v1' },
]

const visionPresets = [
  { label: '与推理模型相同', vision_model: '', vision_base_url: '' },
  { label: '通义千问 VL', vision_model: 'qwen-vl-max', vision_base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1' },
  { label: 'GPT-4o', vision_model: 'gpt-4o', vision_base_url: 'https://api.openai.com/v1' },
  { label: '智谱 GLM-4V', vision_model: 'glm-4v-plus', vision_base_url: 'https://open.bigmodel.cn/api/paas/v4' },
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
          <p className="muted">支持 DeepSeek、智谱 GLM、Kimi、通义千问等国产模型，及任意 OpenAI 兼容接口</p>
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

        <div style={{ marginTop: 24, paddingTop: 20, borderTop: '1px solid var(--line)' }}>
          <h3 style={{ margin: '0 0 4px' }}>翻译专用模型</h3>
          <p className="muted small" style={{ margin: '0 0 12px' }}>
            专用翻译模型（如 qwen-mt-plus）针对翻译优化，速度更快、准确度更高。页面修复和文档问答仍使用上方的推理模型。留空则与推理模型相同。
          </p>
          <label>快捷预设</label>
          <div className="preset-row">
            {translationPresets.map((p) => (
              <button
                key={p.label}
                className="btn ghost"
                onClick={() =>
                  setConfig((c) => ({
                    ...c,
                    translation_model: p.translation_model,
                    translation_base_url: p.translation_base_url,
                  }))
                }
              >
                {p.label}
              </button>
            ))}
          </div>
          <div className="form-grid">
            <label>
              翻译模型名称
              <input
                value={config.translation_model}
                onChange={(e) => setConfig({ ...config, translation_model: e.target.value })}
                placeholder="留空则使用推理模型"
              />
            </label>
            <label>
              翻译模型 API Base URL
              <input
                value={config.translation_base_url}
                onChange={(e) => setConfig({ ...config, translation_base_url: e.target.value })}
                placeholder="留空则使用推理模型地址"
              />
            </label>
            <label className="full">
              翻译模型 API Key
              <input
                type="password"
                value={config.translation_api_key}
                onChange={(e) => setConfig({ ...config, translation_api_key: e.target.value })}
                placeholder="留空则使用推理模型 Key"
              />
            </label>
          </div>
        </div>

        <div style={{ marginTop: 24, paddingTop: 20, borderTop: '1px solid var(--line)' }}>
          <h3 style={{ margin: '0 0 4px' }}>视觉翻译模型</h3>
          <p className="muted small" style={{ margin: '0 0 12px' }}>
            页级视觉翻译主路径：整页截图 + 区块对齐。配置 VL 模型（如 qwen-vl-max / gpt-4o）后启用；未配置时回退文本翻译。
          </p>
          <label>快捷预设</label>
          <div className="preset-row">
            {visionPresets.map((p) => (
              <button
                key={p.label}
                className="btn ghost"
                onClick={() =>
                  setConfig((c) => ({
                    ...c,
                    vision_model: p.vision_model,
                    vision_base_url: p.vision_base_url,
                  }))
                }
              >
                {p.label}
              </button>
            ))}
          </div>
          <div className="form-grid">
            <label>
              视觉模型名称
              <input
                value={config.vision_model ?? ''}
                onChange={(e) => setConfig({ ...config, vision_model: e.target.value })}
                placeholder="如 qwen-vl-max，留空则按主模型名猜测"
              />
            </label>
            <label>
              视觉模型 API Base URL
              <input
                value={config.vision_base_url ?? ''}
                onChange={(e) => setConfig({ ...config, vision_base_url: e.target.value })}
                placeholder="留空则使用推理模型地址"
              />
            </label>
            <label className="full">
              视觉模型 API Key
              <input
                type="password"
                value={config.vision_api_key ?? ''}
                onChange={(e) => setConfig({ ...config, vision_api_key: e.target.value })}
                placeholder="留空则使用推理模型 Key"
              />
            </label>
            <label>
              页图渲染倍率
              <input
                type="number"
                step={0.5}
                min={1}
                max={3}
                value={config.vision_scale ?? 2}
                onChange={(e) => setConfig({ ...config, vision_scale: Number(e.target.value) })}
              />
            </label>
          </div>
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
