/* O envio continua nativo; o progresso apenas informa e evita duplicatas. */
(() => {
  "use strict";

  document.querySelectorAll("[data-generation-form]").forEach((form) => {
    const status = form.querySelector("[data-generation-status]");
    const actions = form.querySelector("[data-generation-actions]");
    const buttons = [...form.querySelectorAll("button[type='submit']")];
    if (!status || !actions || buttons.length === 0) return;
    const initiallyDisabled = new Map(buttons.map((button) => [button, button.disabled]));
    let submitting = false;
    let disableTimer = null;

    form.addEventListener("submit", (event) => {
      if (submitting) {
        event.preventDefault();
        return;
      }
      submitting = true;
      actions.setAttribute("aria-busy", "true");
      status.textContent = event.submitter?.dataset.generationMode === "full"
        ? "Gerando análise completa… aguarde." : "Registrando texto… aguarde.";
      status.hidden = false;
      buttons.forEach((button) => button.setAttribute("aria-disabled", "true"));
      // O navegador primeiro usa o botão acionado para escolher o formaction.
      // Depois disso, as duas ações podem ser desabilitadas sem alterar o envio.
      disableTimer = window.setTimeout(() => {
        buttons.forEach((button) => { button.disabled = true; });
      }, 0);
    });

    window.addEventListener("pageshow", () => {
      window.clearTimeout(disableTimer);
      submitting = false;
      actions.removeAttribute("aria-busy");
      status.hidden = true;
      status.textContent = "";
      buttons.forEach((button) => {
        button.disabled = initiallyDisabled.get(button);
        button.removeAttribute("aria-disabled");
      });
    });
  });
})();
