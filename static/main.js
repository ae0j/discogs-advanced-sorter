function pollingData() {
  return {
    isLoading: false,
    userInput: "",
    submitForm() {
      this.isLoading = true;
      document.getElementById("message").innerHTML = "";
      const inputElement = document.querySelector("input[name='user_input']");
      const userInput = inputElement.value.trim();

      if (!userInput) {
        this.isLoading = false;
        document.getElementById("message").innerHTML =
          "Please enter a valid seller's name";
        return;
      }

      const formData = new FormData(document.querySelector("form"));
      fetch("/", { method: "POST", body: formData })
        .then((response) => response.json())
        .then((data) => {
          document.getElementById("message").innerHTML = data.message;
          if (data.success) {
            this.startTask();
          } else {
            this.isLoading = false;
          }
        });
    },
    startTask() {
      this.isLoading = true;
      this.pollForResult();
    },
    pollForResult() {
      const intervalId = setInterval(() => {
        fetch("/task_status")
          .then((response) => response.json())
          .then((data) => {
            if (data.completed) {
              clearInterval(intervalId);
              this.isLoading = false;
              window.location.href = "/table/";
            }
          });
      }, 3000);
    },
    init() {},
  };
}
