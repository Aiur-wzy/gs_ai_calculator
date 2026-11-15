/** Read imports through the same DSH filesystem/cwd service as native read tools. */
import { createHash } from 'node:crypto'
import { NativeDshDomainError } from './native-dsh-domain-transport.mjs'

export const MAX_INPUT_SNAPSHOT_BYTES = 64 * 1024 * 1024

export async function captureNativeInputSnapshot(ctx, tool, args, exec) {
  if (!['gs_patch_candidate', 'gs_patch_whiteboard'].includes(tool) || args.mode !== 'import') return undefined
  if (ctx.fs === undefined || ctx.workingDirectory === undefined) {
    throw new NativeDshDomainError('input_import_filesystem_unavailable', '本轮DSH文件能力尚未就绪，候选未修改。')
  }
  exec.signal?.throwIfAborted()
  // This is the upstream read tool's public working-directory resolution.
  // Reads inherit the actual backend's policy/errors; no node:fs fallback.
  const cwd = await ctx.workingDirectory.ensure(exec.agent, exec.signal)
  const options = { cwd, signal: exec.signal }
  const target = await ctx.fs.resolve(args.file_path, options)
  const before = await ctx.fs.stat(target, exec.signal)
  if (before?.type !== 'file') throw new NativeDshDomainError('input_edit_request', '输入来源不是可读取的普通文件。')
  if (before.size > MAX_INPUT_SNAPSHOT_BYTES) {
    throw new NativeDshDomainError('input_edit_size', '输入文件超过当前64MiB字节容量。')
  }
  const bytes = Buffer.from(await ctx.fs.readBytes(target, exec.signal, MAX_INPUT_SNAPSHOT_BYTES))
  const afterTarget = await ctx.fs.resolve(args.file_path, options)
  const after = await ctx.fs.stat(afterTarget, exec.signal)
  if (afterTarget.targetKey !== target.targetKey || after?.type !== 'file' || before.version !== after.version) {
    throw new NativeDshDomainError('file_conflict', '读取期间源文件已改变，候选未修改。')
  }
  const sha = createHash('sha256').update(bytes).digest('hex')
  if (args.expected_file_sha !== undefined && args.expected_file_sha !== sha) {
    throw new NativeDshDomainError('file_conflict', '源文件SHA已改变，候选未修改。',
      { details: { expected: args.expected_file_sha, actual: sha } })
  }
  ctx.emit('fs/observed', target, { kind: 'present', version: after.version }, exec)
  return { contract: 'dsh-game-input-file-snapshot-v1', requested_path: args.file_path,
    path: target.displayPath, file_sha: sha, bytes: bytes.length, content_base64: bytes.toString('base64') }
}
