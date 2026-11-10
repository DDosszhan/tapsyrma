document.addEventListener('DOMContentLoaded', () => {
  const automatic = document.getElementById('id_auto_structure');
  const rules = document.getElementById('question_rules-group');
  const fixedQuestions = document.getElementById('questions-group');
  const mode = document.getElementById('id_mode');
  const update = () => {
    const fixed = mode && mode.value === 'fixed';
    if (rules) rules.style.display = fixed || (automatic && automatic.checked) ? 'none' : '';
    if (fixedQuestions) fixedQuestions.style.display = mode && !fixed ? 'none' : '';
    document.querySelectorAll('.field-syllabus_file, .field-syllabus_text, .field-auto_structure').forEach(
      element => { element.style.display = fixed ? 'none' : ''; }
    );
    document.querySelectorAll('.field-question_count, .field-total_points').forEach(
      element => { element.style.display = fixed && !element.classList.contains('readonly') ? 'none' : ''; }
    );
  };
  if (automatic) automatic.addEventListener('change', update);
  if (mode) mode.addEventListener('change', update);
  update();

  const generateSchemes = document.getElementById('generate-schemes');
  if (generateSchemes) generateSchemes.addEventListener('click', async () => {
    const urls = JSON.parse(generateSchemes.dataset.urls);
    const progress = document.getElementById('generate-schemes-progress');
    const csrf = document.querySelector('[name=csrfmiddlewaretoken]')?.value;
    if (!csrf) return;
    generateSchemes.disabled = true;
    let completed = 0;
    const errors = [];
    for (let index = 0; index < urls.length; index += 1) {
      progress.textContent = `Генерация ${index + 1}/${urls.length}…`;
      try {
        const response = await fetch(urls[index], {
          method: 'POST', credentials: 'same-origin',
          headers: { 'X-CSRFToken': csrf, 'X-Requested-With': 'XMLHttpRequest' },
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Ошибка сервера');
        completed += 1;
      } catch (error) {
        errors.push(error.message);
      }
    }
    if (!errors.length) {
      location.reload();
      return;
    }
    progress.textContent = `Готово ${completed}/${urls.length}. ${errors[0]} Обновите страницу и повторите для оставшихся вопросов.`;
    generateSchemes.disabled = false;
  });
  const examForm = document.getElementById('exam_form');
  if (generateSchemes && examForm) {
    const requireSave = () => {
      generateSchemes.disabled = true;
      document.getElementById('generate-schemes-progress').textContent = 'Сначала сохраните изменения.';
    };
    examForm.addEventListener('input', requireSave);
    examForm.addEventListener('change', requireSave);
  }
});
