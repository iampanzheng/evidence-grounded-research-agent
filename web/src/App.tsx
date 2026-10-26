import { useCallback, useEffect, useRef, useState } from 'react';
import type { FormEvent, SVGProps } from 'react';
import { apiBase, checkReadiness, submitResearch } from './api';
import type { ResearchResponse } from './types';
import { citationDisplay, messageForError, messageForResult, SUGGESTED_QUESTIONS } from './view';

function Icon({ name, size = 20, ...props }: SVGProps<SVGSVGElement> & {
  name: 'spark' | 'arrow' | 'search' | 'shield' | 'book' | 'check' | 'link' | 'copy' |
        'refresh' | 'lock' | 'clock' | 'info' | 'x' | 'external' | 'key' | 'chevron' | 'bolt';
  size?: number;
}) {
  const parts = {
    spark: <><path d="m12 3 1.9 6.1L20 11l-6.1 1.9L12 19l-1.9-6.1L4 11l6.1-1.9L12 3Z"/><path d="m19 18 .6 1.5L21 20l-1.4.5L19 22l-.5-1.5L17 20l1.5-.5L19 18Z"/></>,
    arrow: <><path d="M5 12h14"/><path d="m13 6 6 6-6 6"/></>,
    search: <><circle cx="10.9" cy="10.9" r="7"/><path d="m16 16 5 5"/></>,
    shield: <><path d="M12 22s8-3.8 8-10V5l-8-3-8 3v7c0 6.2 8 10 8 10Z"/><path d="m8.5 12 2.5 2.5 4.5-5"/></>,
    book: <><path d="M4 5.5C7 4.2 9.5 4.5 12 6v14c-2.5-1.5-5-1.8-8-.5V5.5Z"/><path d="M20 5.5C17 4.2 14.5 4.5 12 6v14c2.5-1.5 5-1.8 8-.5V5.5Z"/></>,
    check: <path d="m4 12 5 5L20 6"/>,
    link: <><path d="M10 13.5a5 5 0 0 0 7.5.6l2-2a5 5 0 0 0-7-7l-1.2 1.2"/><path d="M14 10.5a5 5 0 0 0-7.5-.6l-2 2a5 5 0 0 0 7 7l1.2-1.2"/></>,
    copy: <><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/></>,
    refresh: <><path d="M20 11a8 8 0 1 0-2 6"/><path d="M20 4v7h-7"/></>,
    lock: <><rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 1 1 8 0v3"/></>,
    clock: <><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>,
    info: <><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/></>,
    x: <path d="M6 6l12 12M18 6 6 18"/>,
    external: <><path d="M14 4h6v6M20 4l-9 9"/><path d="M20 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h5"/></>,
    key: <><circle cx="8" cy="12" r="4"/><path d="M12 12h9m-3 0v3m-3-3v3"/></>,
    chevron: <path d="m6 9 6 6 6-6"/>,
    bolt: <path d="m13 2-9 12h7l-1 8 10-12h-7l1-8Z"/>,
  }[name];
  return <svg aria-hidden="true" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...props}>{parts}</svg>;
}

function newRequestId(): string {
  // Explicitly human-independent, non-repeating IDs; do not expose API key.
  return crypto.randomUUID().replaceAll('-', '');
}

function ResultPanel({ result, onCopy }: { result: ResearchResponse; onCopy: () => void }) {
  const success = result.status === 'ANSWERED';
  const refusal = result.status === 'INSUFFICIENT_EVIDENCE';
  return (
    <section className="result-card" aria-label="研究结果" data-testid="research-result">
      <div className="result-head">
        <div className="result-status">
          <span className={`status-icon ${success ? 'success' : refusal ? 'caution' : 'danger'}`}>
            <Icon name={success ? 'check' : 'shield'} size={18}/>
          </span>
          <div>
            <span className="eyebrow">RESEARCH OUTPUT</span>
            <h2>{messageForResult(result)}</h2>
          </div>
        </div>
        {result.answer && <button className="icon-button" type="button" onClick={onCopy} title="复制回答" aria-label="复制回答"><Icon name="copy" size={18}/></button>}
      </div>
      {(success || refusal) ? <div className="answer-body">{result.answer}</div> :
        <div className="answer-body muted">该请求未返回可验证的答案，请检查服务状态后决定是否重新提交。</div>}
      <div className="result-meta">
        <div className="meta-item"><Icon name="shield" size={15}/> <span>Claim support: NOT_VERIFIED</span></div>
        <div className="meta-item"><Icon name="link" size={15}/> <span>{result.citations.length} 个引用来源</span></div>
      </div>
      {result.citations.length > 0 && (
        <div className="source-section">
          <div className="section-heading"><div><span className="eyebrow">PROVENANCE</span><h3>引用来源</h3></div><span className="count-badge">{result.citations.length}</span></div>
          <ul className="source-list">
            {result.citations.map((citation, index) => {
              const { href, label } = citationDisplay(citation);
              return <li className="source-row" key={`${citation.id}-${index}`}>
                <span className="source-index">{String(index + 1).padStart(2, '0')}</span>
                <div className="source-info"><span className="source-id">{citation.id}</span><span className="source-label">{label}</span></div>
                {href ? <a className="source-link" href={href} rel="noopener noreferrer" referrerPolicy="no-referrer" target="_blank" aria-label={`查看 ${citation.id} 的来源`}><Icon name="external" size={16}/></a> :
                  <span className="source-disabled" title="来源未提供可安全打开的 HTTPS 地址"><Icon name="lock" size={15}/></span>}
              </li>;
            })}
          </ul>
          <p className="source-caveat">引用结构经过校验，但并不代表事实主张已完成语义核查。此界面不展示私有证据原文。</p>
        </div>
      )}
      <details className="technical-details"><summary>执行详情 <Icon name="chevron" size={14}/></summary><dl><div><dt>请求 ID</dt><dd>{result.request_id}</dd></div><div><dt>终止原因</dt><dd>{result.reason_code}</dd></div><div><dt>结果状态</dt><dd>{result.status}</dd></div></dl></details>
    </section>
  );
}

export default function App() {
  const [apiKey, setApiKey] = useState('');
  const [showKey, setShowKey] = useState(false);
  const [question, setQuestion] = useState('');
  const [ready, setReady] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<ResearchResponse | null>(null);
  const [error, setError] = useState('');
  const [clipboardStatus, setClipboardStatus] = useState('');
  const requestController = useRef<AbortController | null>(null);
  const base = apiBase(import.meta.env.VITE_P2_API_URL);

  const refreshHealth = useCallback(async (signal?: AbortSignal) => {
    setReady(null);
    setReady(await checkReadiness(base, fetch, signal));
  }, [base]);

  useEffect(() => {
    const controller = new AbortController();
    void refreshHealth(controller.signal);
    return () => { controller.abort(); requestController.current?.abort(); };
  }, [refreshHealth]);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (loading || !question.trim() || !apiKey.trim() || question.length > 2000) return;
    setLoading(true);
    setError('');
    setResult(null);
    setClipboardStatus('');
    const controller = new AbortController();
    requestController.current = controller;
    try {
      const response = await submitResearch(base, question, apiKey, newRequestId(), fetch, controller.signal);
      if (!controller.signal.aborted) setResult(response);
    } catch (reason: unknown) {
      if (!controller.signal.aborted) setError(messageForError(reason));
    } finally {
      if (requestController.current === controller) requestController.current = null;
      setLoading(false);
    }
  };

  const clear = () => {
    setQuestion('');
    setResult(null);
    setError('');
    setClipboardStatus('');
  };

  const copyAnswer = async () => {
    if (!result?.answer) return;
    try {
      await navigator.clipboard.writeText(result.answer);
      setClipboardStatus('已复制回答');
    } catch {
      setClipboardStatus('复制失败，请手动选择回答文本');
    }
  };

  return (
    <div className="app-shell">
      <aside className="sidebar" aria-label="研究工作区">
        <a className="brand" href="#top" aria-label="P2 Research 首页"><span className="brand-mark"><Icon name="spark" size={24}/></span><span><strong>P2 Research</strong><small>EVIDENCE ENGINE</small></span></a>
        <div className="sidebar-nav-label">WORKSPACE</div>
        <nav aria-label="主导航">
          <a className="sidebar-link active" href="#research"><Icon name="search" size={18}/> 研究工作台 <span className="active-dot"/></a>
          <a className="sidebar-link" href="#connection"><Icon name="key" size={18}/> 连接设置</a>
          <a className="sidebar-link" href="#methodology"><Icon name="shield" size={18}/> 可信度说明</a>
        </nav>
        <div className="sidebar-bottom">
          <div className="mini-panel"><span className="mini-visual"><Icon name="book" size={19}/></span><strong>Evidence-grounded</strong><p>来源可追溯，失败可解释。保持对研究结论的审慎。</p></div>
          <div className="sidebar-footer"><span className="footer-led"/> Local workspace <span>· v0.1</span></div>
        </div>
      </aside>

      <main className="main" id="top">
        <header className="topbar"><div className="breadcrumb">WORKSPACE <span>/</span> <strong>研究工作台</strong></div><div className={`connection-chip ${ready ? 'online' : ready === false ? 'offline' : ''}`} aria-live="polite"><span className="connection-dot"/>{ready === null ? '正在检查服务' : ready ? '本地服务就绪' : '本地服务未就绪'}</div></header>
        <div className="content" id="research">
          <section className="hero"><div className="hero-kicker"><Icon name="spark" size={16}/> EVIDENCE-FIRST RESEARCH</div><h1>从问题出发，<br/><em>让证据支撑答案。</em></h1><p>当前公开演示使用原创的虚构 Orion 文档，展示真实检索、受控工作流与引用结构校验。无法确认时会拒答；模型回答为确定性模拟，并不证明语义真实性。</p></section>
          <section className="workspace-grid">
            <div className="primary-column">
              <form className="query-card" onSubmit={handleSubmit} aria-label="提交研究问题">
                <div className="query-card-head"><div><span className="eyebrow">NEW RESEARCH</span><h2>开始一项研究</h2></div><span className="card-icon"><Icon name="search" size={20}/></span></div>
                <label className="field-label" htmlFor="question">你的问题</label>
                <textarea id="question" value={question} onChange={event => setQuestion(event.target.value)} maxLength={2000} placeholder="例如：What checks gate a fictional Orion release?" rows={5} disabled={loading}/>
                <div className="editor-footer"><span>使用自然语言提问，系统会检索可验证的文档证据。</span><span className="character-counter">{question.length} / 2000</span></div>
                <div className="suggestions"><span>快速尝试</span><div className="suggestion-list">{SUGGESTED_QUESTIONS.map(item => <button key={item} type="button" onClick={() => setQuestion(item)} disabled={loading}>{item}<Icon name="arrow" size={13}/></button>)}</div></div>
                <div className="query-actions"><button className="subtle-button" type="button" onClick={clear} disabled={loading}><Icon name="x" size={15}/> 清空</button><button className="primary-button" type="submit" disabled={loading || !question.trim() || !apiKey.trim() || ready !== true}>{loading ? <><span className="spinner"/> 正在研究…</> : <><Icon name="spark" size={17}/> 开始研究 <Icon name="arrow" size={17}/></>}</button></div>
                {ready !== true && <p className="inline-hint" role="status">需要本地后端就绪才能提交研究问题。</p>}
              </form>
              <div aria-live="polite" aria-atomic="true" className="result-area">
                {error && <div className="error-panel" role="alert"><Icon name="info" size={20}/><div><strong>本次请求未完成</strong><p>{error}</p></div></div>}
                {loading && <div className="loading-panel" role="status"><span className="spinner large"/><div><strong>正在运行有边界的研究工作流</strong><p>检索证据 → 生成回答 → 检查引用。服务可能需要一些时间。</p></div></div>}
                {result && <ResultPanel result={result} onCopy={() => { void copyAnswer(); }}/>} 
                {clipboardStatus && <p className="copy-message" role="status">{clipboardStatus}</p>}
              </div>
            </div>
            <div className="context-column">
              <section className="side-card" id="connection"><div className="side-card-header"><div className="side-icon"><Icon name="key" size={19}/></div><h2>连接到本地服务</h2></div><p className="side-description">使用 S6.2 本地 API 的独立访问密钥。密钥只保留在当前页面内存中。</p><label className="field-label" htmlFor="api-key">Local API Key</label><div className="secret-field"><input id="api-key" type={showKey ? 'text' : 'password'} value={apiKey} onChange={event => setApiKey(event.target.value)} autoComplete="off" spellCheck={false} placeholder="输入 P2_API_KEY"/><button type="button" onClick={() => setShowKey(!showKey)} aria-label={showKey ? '隐藏密钥' : '显示密钥'}>{showKey ? '隐藏' : '显示'}</button></div><button className="refresh-button" type="button" onClick={() => { void refreshHealth(); }}><Icon name="refresh" size={15}/> 重新检查连接</button><div className="connection-endpoint"><span>API ENDPOINT</span><code>{base}</code></div><p className="security-note"><Icon name="lock" size={14}/> 仅本地访问，不会持久化密钥</p></section>
              <section className="side-card method-card" id="methodology"><div className="side-card-header"><div className="side-icon"><Icon name="shield" size={19}/></div><h2>研究方法</h2></div><div className="method-row"><span><Icon name="search" size={16}/></span><div><strong>BM25 检索</strong><small>默认检索后端，取回关联文档片段</small></div></div><div className="method-row"><span><Icon name="bolt" size={16}/></span><div><strong>有边界的工作流</strong><small>控制模型调用、修复和终止条件</small></div></div><div className="method-row"><span><Icon name="link" size={16}/></span><div><strong>引用结构校验</strong><small>拒绝无法合法关联来源的答案</small></div></div><div className="method-note"><Icon name="info" size={16}/><p>结构校验不等于语义事实验证。请独立核对重要结论及来源。</p></div></section>
              <div className="side-small-print"><Icon name="clock" size={15}/> 后端默认限制请求频率。为避免重复计费，前端不会自动重试。</div>
            </div>
          </section>
        </div>
        <footer className="main-footer"><span>P2 · Evidence-grounded Research Agent</span><span>Built for traceability, not guesswork.</span></footer>
      </main>
    </div>
  );
}
