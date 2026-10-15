import test from 'node:test';
import assert from 'node:assert/strict';
import { resolveShortcut } from '../../frontend/js/shortcuts.mjs';

const review = {reviewOpen:true, busy:false, dialog:null, editing:false, singleKeys:true};
const event = (code, extra={}) => ({code,key:code,ctrlKey:false,metaKey:false,altKey:false,shiftKey:false,
  repeat:false,isComposing:false,defaultPrevented:false,...extra});

test('workspace keys include non-Latin keyboard layouts and page navigation', () => {
  assert.equal(resolveShortcut(event('KeyT',{key:'е'}),review),'text');
  assert.equal(resolveShortcut(event('KeyC',{key:'с'}),review),'confirmBulk');
  assert.equal(resolveShortcut(event('KeyI'),review),'ignoreBulk');
  assert.equal(resolveShortcut(event('KeyR'),review),'draw');
  assert.equal(resolveShortcut(event('KeyS'),review),'select');
  assert.equal(resolveShortcut(event('KeyE'),review),'reviewText');
  assert.equal(resolveShortcut(event('KeyA'),review),'selectPage');
  assert.equal(resolveShortcut(event('KeyX'),review),'clearBulk');
  assert.equal(resolveShortcut(event('BracketLeft',{key:'х'}),review),'previous');
  assert.equal(resolveShortcut(event('BracketRight',{key:'ъ'}),review),'next');
});

test('typing and unrelated browser or OS combinations are left alone', () => {
  for (const code of ['KeyT','KeyR','KeyS','KeyE','KeyA','KeyC','KeyI','KeyX','BracketRight']) {
    assert.equal(resolveShortcut(event(code),{...review,editing:true}),null);
    assert.equal(resolveShortcut(event(code,{ctrlKey:true}),review),null);
    assert.equal(resolveShortcut(event(code,{metaKey:true}),review),null);
    assert.equal(resolveShortcut(event(code,{altKey:true}),review),null);
    assert.equal(resolveShortcut(event(code,{shiftKey:true}),review),null);
  }
  assert.equal(resolveShortcut(event('Enter',{ctrlKey:true}),{...review,editing:true}),null);
});

test('generate requires a loaded review; confirm is scoped to the review dialog', () => {
  for (const modifier of ['ctrlKey','metaKey']) {
    assert.equal(resolveShortcut(event('Enter',{[modifier]:true}),review),'generate');
    assert.equal(resolveShortcut(event('Enter',{[modifier]:true}),{...review,dialog:'review',editing:true}),'confirmRegion');
    assert.equal(resolveShortcut(event('Enter',{[modifier]:true}),{...review,dialog:'other'}),null);
    assert.equal(resolveShortcut(event('Enter',{[modifier]:true}),{...review,reviewOpen:false}),null);
  }
});

test('processing, held keys and composition cannot trigger another mutation', () => {
  assert.equal(resolveShortcut(event('KeyI'),{...review,busy:true}),null);
  for (const flag of ['repeat','isComposing','defaultPrevented']) {
    assert.equal(resolveShortcut(event('Enter',{ctrlKey:true,[flag]:true}),review),null);
    assert.equal(resolveShortcut(event('KeyC',{[flag]:true}),review),null);
  }
});

test('modal dialogs retain native Escape, arrows, typing and bulk shortcuts', () => {
  for (const dialog of ['review','other']) {
    for (const code of ['Escape','KeyI','KeyC','BracketRight','ArrowLeft']) {
      assert.equal(resolveShortcut(event(code),{...review,dialog}),null);
    }
  }
});

test('single-key preference preserves generate, cancel and help', () => {
  const disabled={...review,singleKeys:false};
  assert.equal(resolveShortcut(event('KeyI'),disabled),null);
  assert.equal(resolveShortcut(event('BracketRight'),disabled),null);
  assert.equal(resolveShortcut(event('Enter',{metaKey:true}),disabled),'generate');
  assert.equal(resolveShortcut(event('Escape'),disabled),'cancel');
  assert.equal(resolveShortcut(event('Slash',{key:'?',shiftKey:true}),disabled),'help');
  assert.equal(resolveShortcut(event('Slash',{key:'?',shiftKey:true}),{...disabled,reviewOpen:false}),'help');
});

test('upload chord opens the chooser only on the initial screen', () => {
  const initial={...review,reviewOpen:false,uploadOpen:true};
  for (const modifier of ['ctrlKey','metaKey']) {
    assert.equal(resolveShortcut(event('KeyU',{[modifier]:true}),initial),'upload');
    assert.equal(resolveShortcut(event('KeyU',{[modifier]:true}),review),null);
    assert.equal(resolveShortcut(event('KeyU',{[modifier]:true}),{...initial,editing:true}),null);
    assert.equal(resolveShortcut(event('KeyU',{[modifier]:true}),{...initial,dialog:'other'}),null);
    assert.equal(resolveShortcut(event('KeyU',{[modifier]:true,shiftKey:true}),initial),null);
    assert.equal(resolveShortcut(event('KeyU',{[modifier]:true}),{...initial,busy:true}),null);
  }
});

test('Enter redacts only a native text selection and respects focused controls', () => {
  for (const code of ['Enter','NumpadEnter']) {
    assert.equal(resolveShortcut(event(code),{...review,hasSelectedText:true}),'reviewText');
    assert.equal(resolveShortcut(event(code),{...review,hasSelectedText:true,singleKeys:false}),'reviewText');
    assert.equal(resolveShortcut(event(code),review),null);
    assert.equal(resolveShortcut(event(code),{...review,hasSelectedText:true,controlFocused:true}),null);
    assert.equal(resolveShortcut(event(code),{...review,hasSelectedText:true,editing:true}),null);
    assert.equal(resolveShortcut(event(code),{...review,hasSelectedText:true,dialog:'review'}),null);
    assert.equal(resolveShortcut(event(code,{shiftKey:true}),{...review,hasSelectedText:true}),null);
  }
});
