/** Resolve commands without intercepting typing, browser shortcuts or modal keys. */
export function resolveShortcut(event, context) {
  if (event.defaultPrevented || event.isComposing || event.repeat || context.busy || event.altKey) return null;
  const modifier = event.ctrlKey || event.metaKey;
  const code = event.code || (event.key.length === 1 ? `Key${event.key.toUpperCase()}` : event.key);
  if (modifier) {
    if (code === 'KeyU' && !event.shiftKey && context.uploadOpen && !context.dialog && !context.editing) return 'upload';
    if (!['Enter', 'NumpadEnter'].includes(code) || event.shiftKey) return null;
    if (context.dialog === 'review') return 'confirmRegion';
    if (!context.dialog && context.reviewOpen && !context.editing) return 'generate';
    return null;
  }
  if (context.dialog || context.editing) return null;
  if (event.key === '?' || (code === 'Slash' && event.shiftKey)) return 'help';
  if (!context.reviewOpen) return null;
  if (code === 'Escape') return 'cancel';
  if (['Enter', 'NumpadEnter'].includes(code) && !event.shiftKey && context.hasSelectedText && !context.controlFocused) return 'reviewText';
  if (!context.singleKeys || event.shiftKey) return null;
  return {
    KeyT: 'text', KeyR: 'draw', KeyS: 'select', KeyE: 'reviewText',
    KeyA: 'selectPage', KeyC: 'confirmBulk', KeyI: 'ignoreBulk', KeyX: 'clearBulk',
    BracketLeft: 'previous', BracketRight: 'next',
  }[code] || null;
}
