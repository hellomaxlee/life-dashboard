  var widget_code = '  <div class=\"gr_custom_container_\">\n    <h2 class=\"gr_custom_header_\">\n    <a style=\"text-decoration: none;\" rel=\"nofollow\" href=\"https:\/\/www.goodreads.com\/review\/list\/1-reader?shelf=read&amp;utm_medium=api&amp;utm_source=custom_widget\">Reader\'s bookshelf: read<\/a>\n    <\/h2>\n  <br style=\"clear: both\"\/>\n  <center>\n    <a rel=\"nofollow\" href=\"https:\/\/www.goodreads.com\/\"><img alt=\"goodreads.com\" style=\"border:0\" src=\"https:\/\/s.gr-assets.com\/images\/widget\/widget_logo.gif\" \/><\/a>\n  <\/center>\n  <noscript>\n    Share <a rel=\"nofollow\" href=\"https:\/\/www.goodreads.com\/\">book reviews<\/a> and ratings with Reader, and even join a <a rel=\"nofollow\" href=\"https:\/\/www.goodreads.com\/group\">book club<\/a> on Goodreads.\n  <\/noscript>\n  <\/div>\n'
  var widget_div = document.getElementById('gr_custom_widget_')
  if (widget_div) {
    widget_div.innerHTML = widget_code
  }
  else {
    document.write(widget_code)
  }
