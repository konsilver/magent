/**
 * 解析 execute_code / run_command 工具的输出。
 *
 * 后端 _call_sidecar 返回的格式：
 *   stdout:\n<content>\n\nstderr:\n<content>\n\nexit_code: N\n\nexecution_time: Nms
 *
 * Phase 2 会在末尾追加：
 *   files: [{file_id, name, url, mime_type, size}, ...]
 */

export interface ExecFileRef {
  file_id: string;
  name: string;
  url: string;
  mime_type: string;
  size: number;
}

export interface ParsedExecResult {
  stdout: string;
  stderr: string;
  exitCode: number;
  executionTimeMs: number;
  files: ExecFileRef[];
}

const EMPTY: ParsedExecResult = {
  stdout: '',
  stderr: '',
  exitCode: -1,
  executionTimeMs: 0,
  files: [],
};

export function parseExecOutput(raw: unknown): ParsedExecResult {
  if (!raw) return EMPTY;

  // 如果已经是结构化 JSON 对象
  if (typeof raw === 'object' && raw !== null) {
    const obj = raw as Record<string, unknown>;
    if ('exit_code' in obj || 'exitCode' in obj) {
      return {
        stdout: String(obj.stdout ?? ''),
        stderr: String(obj.stderr ?? ''),
        exitCode: Number(obj.exit_code ?? obj.exitCode ?? -1),
        executionTimeMs: Number(obj.execution_time_ms ?? obj.executionTimeMs ?? obj.execution_time ?? 0),
        files: Array.isArray(obj.files) ? obj.files : [],
      };
    }
    // 可能是 JSON 字符串包在 result 里
    if (typeof obj.result === 'string') {
      return parseExecOutput(obj.result);
    }
    if (typeof obj.result === 'object') {
      return parseExecOutput(obj.result);
    }
  }

  if (typeof raw !== 'string') return EMPTY;

  const text = raw as string;

  // 尝试 JSON 解析
  if (text.startsWith('{')) {
    try {
      return parseExecOutput(JSON.parse(text));
    } catch { /* fall through */ }
  }

  let stdout = '';
  let stderr = '';
  let exitCode = 0;
  let executionTimeMs = 0;
  let files: ExecFileRef[] = [];

  const filesMatch = text.match(/\nfiles:\s*(\[[\s\S]*\])\s*$/);
  if (filesMatch) {
    try { files = JSON.parse(filesMatch[1]); } catch { /* ignore */ }
  }

  for (const seg of text.split('\n\n')) {
    const trimmed = seg.trim();
    if (trimmed.startsWith('stdout:')) {
      stdout = trimmed.slice('stdout:'.length).trimStart();
    } else if (trimmed.startsWith('stderr:')) {
      stderr = trimmed.slice('stderr:'.length).trimStart();
    } else if (trimmed.startsWith('exit_code:')) {
      const val = parseInt(trimmed.slice('exit_code:'.length).trim(), 10);
      if (!isNaN(val)) exitCode = val;
    } else if (trimmed.startsWith('execution_time:')) {
      const m = trimmed.match(/(\d+)\s*ms/);
      if (m) executionTimeMs = parseInt(m[1], 10);
    }
  }

  return { stdout, stderr, exitCode, executionTimeMs, files };
}

/**
 * 从 ToolCall.input 中提取代码和语言。
 */
export function extractCodeFromInput(
  toolName: string,
  input: unknown,
): { code: string; language: string } {
  // Handle JSON string input (SSE may deliver args as string)
  let obj: Record<string, unknown> | null = null;
  if (typeof input === 'string') {
    try { obj = JSON.parse(input); } catch { /* not JSON */ }
    if (!obj) return { code: input, language: 'text' };
  } else if (input && typeof input === 'object') {
    obj = input as Record<string, unknown>;
  }
  if (!obj) return { code: '', language: 'text' };

  if (toolName === 'run_command') {
    return { code: String(obj.command ?? ''), language: 'bash' };
  }
  return {
    code: String(obj.code ?? ''),
    language: String(obj.language ?? 'python'),
  };
}
