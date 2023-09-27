$(document).ready(function () {
  var url = $(".container").data("url");
  var table = $("#myTable").DataTable({
    processing: true,
    serverSide: true,
    responsive: true,
    ajax: {
      url: url,
      type: "POST",
    },
    order: [[4, "desc"]],
    pageLength: 250,
    deferRender: true,
    searching: false,
    lengthChange: false,
    columnDefs: [
      { targets: "_all", orderSequence: ["desc", "asc"] },
      {
        targets: 8,
        orderable: false,
        render: function (data, type, row, meta) {
          if (type === "display") {
            return (
              '<a href="https://www.discogs.com' +
              data +
              '" target="_blank">View</a>'
            );
          }
          return data;
        },
      },
      {
        targets: 6,
        orderable: false,
      },
    ],
  });

  $("#goToPage").on("click", function () {
    var pageNum = $("#pageNumber").val();
    if (pageNum) {
      pageNum = parseInt(pageNum, 10);
      var pageInfo = table.page.info();
      if (pageNum > 0 && pageNum <= pageInfo.pages) {
        table.page(pageNum - 1).draw(false);
      } else {
        alert("Invalid page number!");
      }
    }
  });
});
