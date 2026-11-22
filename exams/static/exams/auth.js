for (const button of document.querySelectorAll(".auth-toggle")) {
    const field = document.getElementById(button.getAttribute("aria-controls"));
    const label = button.firstElementChild;
    button.hidden = false;

    button.addEventListener("click", () => {
        const show = field.type === "password";
        field.type = show ? "text" : "password";
        label.textContent = show ? "Hide" : "Show";
    });
}

const form = document.querySelector(".auth-form");
const submit = form.querySelector(".auth-submit");
const submitLabel = submit.firstElementChild;
const idleText = submitLabel.textContent;

form.addEventListener("submit", (event) => {
    if (submit.disabled) {
        event.preventDefault();
        return;
    }
    submitLabel.textContent = submit.dataset.pending;
    submit.disabled = true;
});

window.addEventListener("pageshow", () => {
    submitLabel.textContent = idleText;
    submit.disabled = false;
});
