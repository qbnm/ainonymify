/* Optional accounts use server cookies; personal text never enters localStorage. */
export function initAccounts({api, types, hasSession}) {
  const $ = id => document.getElementById(id);
  let account = null, rules = [], editing = false;
  for (const type of types) {
    const o = document.createElement('option'); o.value = type; o.textContent = type;
    $('rule-type').append(o);
  }
  $('rule-type').value = 'ADDRESS';
  const feedback = text => { $('account-feedback').textContent = text; };
  function display() {
    $('account-button').textContent = account ? `${account.username} · Rules` : 'Guest · Account';
    $('account-button').disabled = false;
    $('identity-summary').textContent = account ? `Signed in as ${account.username} · Personal rules and explicit decisions are remembered.` : 'Guest mode · Decisions are not saved. Create an optional account to remember personal rules.';
    $('learning-summary').textContent = account ? 'Signed in: Confirm / Ignore decisions are saved for future uploads. Manage them in Rules above.' : 'Guest mode: decisions apply only to this temporary session.';
    $('account-guest').hidden = Boolean(account); $('account-member').hidden = !account;
    $('account-name').textContent = account ? `Signed in as ${account.username}` : '';
    for (const warning of document.querySelectorAll('.account-switch-warning')) warning.hidden = !hasSession();
    $('account-delete-confirm').hidden = true;
  }
  async function action(fn) {
    feedback('');
    const buttons = [...$('account-dialog').querySelectorAll('button')];
    for (const b of buttons) b.disabled = true;
    try { await fn(); } catch (error) { feedback(error.message); }
    finally { for (const b of buttons) b.disabled = false; }
  }
  function resetForm() {
    editing = false; $('personal-rule-form').reset(); $('rule-text').readOnly = false;
    $('rule-type').value = 'ADDRESS'; $('rule-form-title').textContent = 'Add a personal rule'; $('rule-cancel').hidden = true;
  }
  function drawRules() {
    $('saved-rules-title').textContent = `Saved rules (${rules.length} / 200)`;
    $('saved-rules').replaceChildren();
    if (!rules.length) {
      const p = document.createElement('p'); p.className = 'hint'; p.textContent = 'No saved rules yet. Add one below or confirm / ignore a region while signed in.'; $('saved-rules').append(p);
    }
    for (const rule of rules) {
      const row = document.createElement('div'); row.className = 'saved-rule';
      const body = document.createElement('div');
      const title = document.createElement('strong'); title.textContent = `${rule.action === 'ignore' ? 'Ignore' : 'Replace'} · ${rule.label || rule.type}`;
      const text = document.createElement('span'); text.textContent = rule.text;
      const kind = document.createElement('small'); kind.textContent = rule.type;
      body.append(title, text, kind);
      const controls = document.createElement('div'); controls.className = 'saved-rule-actions';
      const edit = document.createElement('button'); edit.textContent = 'Edit';
      edit.onclick = () => {
        editing = true; $('rule-text').value = rule.text; $('rule-text').readOnly = true;
        $('rule-type').value = rule.type; $('rule-action').value = rule.action; $('rule-label').value = rule.label;
        $('rule-form-title').textContent = 'Edit saved rule'; $('rule-cancel').hidden = false;
        $('rule-label').focus();
      };
      const remove = document.createElement('button'); remove.textContent = 'Forget';
      remove.setAttribute('aria-label', `Forget ${rule.label || rule.type}: ${rule.text}`);
      remove.onclick = () => action(async () => {
        rules = (await (await api(`/api/account/rules/${rule.id}`, {method:'DELETE'})).json()).rules;
        resetForm(); drawRules(); feedback('Rule forgotten. New uploads will use the remaining rules.');
      });
      controls.append(edit, remove); row.append(body, controls); $('saved-rules').append(row);
    }
  }
  async function refresh() {
    account = (await (await api('/api/account')).json()).account;
    display();
  }
  $('account-button').onclick = async () => {
    feedback(''); resetForm(); $('account-dialog').showModal();
    await action(async () => {
      await refresh();
      if (account) { rules = (await (await api('/api/account/rules')).json()).rules; drawRules(); }
    });
  };
  $('account-close').onclick = $('continue-guest').onclick = () => $('account-dialog').close();
  $('account-auth-form').onsubmit = event => {
    event.preventDefault(); const mode = event.submitter?.value === 'register' ? 'register' : 'login';
    action(async () => {
      await api(`/api/account/${mode}`, {method:'POST',data:{username:$('account-username').value,password:$('account-password').value}});
      $('account-password').value = ''; location.reload();
    });
  };
  $('account-logout').onclick = () => action(async () => { await api('/api/account/login', {method:'DELETE'}); location.reload(); });
  $('account-delete-start').onclick = () => { $('account-delete-confirm').hidden = false; $('account-delete-final').focus(); };
  $('account-delete-cancel').onclick = () => { $('account-delete-confirm').hidden = true; };
  $('account-delete-final').onclick = () => action(async () => { await api('/api/account', {method:'DELETE'}); location.reload(); });
  $('rule-cancel').onclick = resetForm;
  $('personal-rule-form').onsubmit = event => {
    event.preventDefault();
    action(async () => {
      const wasEditing = editing;
      rules = (await (await api('/api/account/rules', {method:'POST',data:{text:$('rule-text').value,type:$('rule-type').value,label:$('rule-label').value,action:$('rule-action').value}})).json()).rules;
      resetForm(); drawRules(); feedback(wasEditing ? 'Rule updated for future uploads.' : 'Rule saved for future uploads.');
    });
  };
  const ready = refresh().catch(error => { $('account-button').disabled = false; feedback(error.message); });
  return {ready, refresh, current:()=>account};
}
