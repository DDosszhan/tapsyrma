document.addEventListener('DOMContentLoaded', () => {
  const automatic = document.getElementById('id_auto_structure');
  const rules = document.getElementById('question_rules-group');
  if (!automatic || !rules) return;
  const update = () => { rules.style.display = automatic.checked ? 'none' : ''; };
  automatic.addEventListener('change', update);
  update();
});
