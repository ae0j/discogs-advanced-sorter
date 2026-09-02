function pollingData() {
  return {
    isLoading: false,
    userInput: "",
    phase: "queued",
    processed: 0,
    total: null,
    elapsed: 0,
    idleFor: 0,
    progressPercent() {
      if (!this.total) return 0;
      if (this.phase === "finalizing" || this.phase === "complete") return 100;
      return Math.min(99, Math.floor((this.processed / this.total) * 100));
    },
    loadingText() {
      const seconds = Number.isFinite(this.elapsed) ? ` · ${this.elapsed}s` : "";
      if (this.phase === "queued") return `Queued${seconds}`;
      if (this.phase === "checking") return `Checking Discogs inventory${seconds}`;
      if (this.phase === "finalizing") {
        const count = this.total ? this.total.toLocaleString() : "the";
        return `Preparing ${count} results${seconds}`;
      }
      if (this.total) {
        const waiting = this.idleFor >= 10 ? " · waiting for Discogs" : "";
        return `${this.processed.toLocaleString()} / ${this.total.toLocaleString()} listings · ${this.progressPercent()}%${seconds}${waiting}`;
      }
      return `Digging through crates${seconds}`;
    },
    setMessage(message, isError = false) {
      const messageNode = document.getElementById("message");
      messageNode.textContent = message || "";
      messageNode.style.color = isError ? "#b91c1c" : "#0a5a54";
    },
    submitForm() {
      this.isLoading = true;
      this.phase = "queued";
      this.processed = 0;
      this.total = null;
      this.elapsed = 0;
      this.idleFor = 0;
      this.setMessage("");
      const sellerInput = document
        .querySelector("input[name='user_input']")
        .value.trim();
      const filteredUrlInput = document
        .querySelector("input[name='filtered_url']")
        .value.trim();

      if (!sellerInput && !filteredUrlInput) {
        this.isLoading = false;
        this.setMessage(
          "Please enter a seller name or a Discogs /sell/list or /seller/username/profile URL",
          true
        );
        return;
      }

      const formData = new FormData(document.querySelector("form"));
      fetch("/", { method: "POST", body: formData })
        .then((response) => response.json())
        .then((data) => {
          this.setMessage(data.message, !data.success);
          if (data.success) {
            this.uniqueId = data.unique_id;
            this.pollForResult();
          } else {
            this.isLoading = false;
          }
        });
    },
    startTask() {
      this.isLoading = true;
      this.pollForResult();
      fetch("/get_unique_id")
        .then((response) => response.json())
        .then((data) => {
          const unique_id = data.unique_id;
          if (unique_id) {
            pollForResult(unique_id);
          }
        });
    },
    pollForResult() {
      const poll = () => {
        fetch(`/task_status/${this.uniqueId}`)
          .then(async (response) => {
            const data = await response.json();
            if (!response.ok || data.completed === null) {
              throw new Error(data.error || "The task is no longer available");
            }
            return data;
          })
          .then((data) => {
            this.phase = data.phase || this.phase;
            this.processed = Number(data.processed || 0);
            this.total = data.total == null ? null : Number(data.total);
            this.elapsed = data.elapsed == null ? this.elapsed : Number(data.elapsed);
            this.idleFor = data.idle_for == null ? 0 : Number(data.idle_for);
            if (data.completed) {
              if (data.error) {
                this.isLoading = false;
                this.setMessage(data.error, true);
                return;
              }
              this.isLoading = false;
              window.location.href = `/table/${this.uniqueId}`;
              return;
            }
            setTimeout(poll, 1000);
          })
          .catch((error) => {
            this.isLoading = false;
            this.setMessage(error.message || "The task could not be checked", true);
          });
      };
      poll();
    },
    init() {},
  };
}
