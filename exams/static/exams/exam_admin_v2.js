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
});
