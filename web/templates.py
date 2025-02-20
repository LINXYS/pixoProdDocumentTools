# HTML template for the main page
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Project Interface</title>
</head>
<body>
    <h2>Upload Files</h2>
    <form method="POST" action="/upload" enctype="multipart/form-data">
      <input type="hidden" name="token" value="{{ token }}" />
      <input type="file" name="upload_files" multiple />
      <button type="submit">Upload Files</button>
    </form>
    <hr>
    <h2>Start Process</h2>
    <form method="POST" action="/start">
      <input type="hidden" name="token" value="{{ token }}" />
      <button type="submit">Start</button>
    </form>
    <hr>
    <h2>Finish and Shut Down Server</h2>
    <form method="POST" action="/shutdown">
      <input type="hidden" name="token" value="{{ token }}" />
      <button type="submit">Finish</button>
    </form>
</body>
</html>
"""

# HTML template to display upload results
UPLOAD_RESULT_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Upload Results</title>
</head>
<body>
    <h2>Files uploaded successfully:</h2>
    <ul>
      {% for file in uploaded_filenames %}
         <li>{{ file }}</li>
      {% endfor %}
    </ul>
    <button onclick="window.location.href='/?token={{ token }}'">Back</button>
</body>
</html>
"""
