import { ApiError, type Citation, type ResearchResponse } from './types';
import { safeSourceUrl } from './api';

export function messageForError(error: unknown): string {
  if (!(error instanceof ApiError)) return '请求未能完成，请确认本地服务状态。';
  switch (error.kind) {
    case 'UNAUTHORIZED': return '访问密钥不正确。请检查本地 API Key。';
    case 'NOT_READY': return '研究服务尚未就绪，请检查后端启动和冻结语料。';
    case 'BAD_REQUEST': return '请求未被接受，请检查问题内容或请求格式。';
    case 'VERIFICATION_FAILED': return '回答未通过引用验证，系统已阻止输出未经校验的结论。';
    case 'RATE_LIMITED': return error.retryAfter
      ? `请求过于频繁，请至少等待 ${error.retryAfter} 秒后再手动提交。`
      : '请求过于频繁，请稍后手动重试。';
    case 'MALFORMED_RESPONSE': return '服务返回的数据未通过安全校验，未展示任何结果。';
    case 'CANCELLED': return '已停止等待。远端请求可能仍在执行或计费，请勿立即重复提交。';
    case 'NETWORK_ERROR': return '无法连接到本地研究服务。请检查后端地址和 CORS 设置。';
    default: return '研究请求失败，请检查本地服务。';
  }
}

export function messageForResult(result: ResearchResponse): string {
  switch (result.status) {
    case 'ANSWERED': return '研究完成 · 引用结构已校验';
    case 'INSUFFICIENT_EVIDENCE': return '证据不足 · 已安全拒答';
    case 'CONFLICTING_EVIDENCE': return '证据存在冲突 · 无法可靠回答';
    case 'VERIFICATION_FAILED': return '验证未通过 · 未输出未经校验的结论';
    default: return '服务处理失败 · 未输出未经校验的结论';
  }
}

export function citationDisplay(citation: Citation): { href: string | null; label: string } {
  return {
    href: safeSourceUrl(citation.source_url),
    label: citation.document_id && citation.chunk_id
      ? `${citation.document_id} · ${citation.chunk_id}`
      : citation.id,
  };
}

export const SUGGESTED_QUESTIONS = [
  'What checks gate a fictional Orion release?',
  'How much revenue did Orion earn yesterday?',
  'What are the fictional Orion incident response steps?',
];
