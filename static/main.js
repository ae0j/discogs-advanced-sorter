function pollingData() {
  return {
    isLoading: false,
    userInput: "",
    setMessage(message, isError = false) {
      const messageNode = document.getElementById("message");
      messageNode.innerHTML = message || "";
      messageNode.style.color = isError ? "#b91c1c" : "#0a5a54";
    },
    submitForm() {
      this.isLoading = true;
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
      const intervalId = setInterval(() => {
        fetch(`/task_status/${this.uniqueId}`)
          .then((response) => response.json())
          .then((data) => {
            if (data.completed) {
              clearInterval(intervalId);
              if (data.error) {
                this.isLoading = false;
                this.setMessage(data.error, true);
                return;
              }
              this.isLoading = false;
              window.location.href = `/table/${this.uniqueId}`;
            }
          });
      }, 3000);
    },
    init() {},
  };
}
