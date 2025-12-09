# HTML template for the main page
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Project Interface</title>
    <style>
      label { display: block; margin-top: 4px; }
    </style>
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
      <div style="margin:8px 0;">
        <label for="urls"><strong>List of URLs</strong> (newline separated)</label><br>
        <textarea id="urls" name="urls" rows="6" cols="80" placeholder="https://example.com/page-1
https://example.com/page-2">{{ urls_text or "" }}</textarea>
      </div>
      <div style="margin:8px 0;">
        <label for="sitemap_url"><strong>Sitemap URL</strong></label><br>
        <input id="sitemap_url" name="sitemap_url" type="url" size="80" placeholder="https://example.com/sitemap.xml" value="{{ sitemap_url or '' }}" />
      </div>

      <!-- NEW: Optional CSS selector -->
      <div style="margin:8px 0;">
        <label for="css_selector"><strong>CSS selector (optional)</strong></label><br>
        <input id="css_selector" name="css_selector" type="text" size="80"
               placeholder="div.cms-block.pos-1.cms-block-text.position-relative"
               value="{{ css_selector or '' }}" />
        <div style="font-size:12px;color:#666;margin-top:4px;">
          Enter all classes of the div here. If provided, only text inside matching elements is extracted.
        </div>
      </div>

      <hr>
      <h3>Remote Files (FTP / FTPS / SFTP)</h3>
      <div style="font-size:12px;color:#666;margin-bottom:4px;">
        Configure an optional remote source to fetch files into the local <code>files/</code> directory
        before ingestion. Leave empty to skip.
      </div>
      <div style="margin:8px 0;">
        <label for="remote_protocol"><strong>Protocol</strong></label>
        <select id="remote_protocol" name="remote_protocol">
          <option value="" {% if not remote_protocol %}selected{% endif %}>(none)</option>
          <option value="ftp" {% if remote_protocol == 'ftp' %}selected{% endif %}>FTP</option>
          <option value="ftps" {% if remote_protocol == 'ftps' %}selected{% endif %}>FTPS (FTP over TLS)</option>
          <option value="sftp" {% if remote_protocol == 'sftp' %}selected{% endif %}>SFTP (SSH)</option>
        </select>
      </div>
      <div style="margin:8px 0;">
        <label for="remote_host"><strong>Host</strong></label>
        <input id="remote_host" name="remote_host" type="text" size="40" value="{{ remote_host or '' }}" />
      </div>
      <div style="margin:8px 0;">
        <label for="remote_port"><strong>Port</strong> (optional)</label>
        <input id="remote_port" name="remote_port" type="number" size="6" value="{{ remote_port or '' }}" />
      </div>
      <div style="margin:8px 0;">
        <label for="remote_username"><strong>Username</strong></label>
        <input id="remote_username" name="remote_username" type="text" size="40" value="{{ remote_username or '' }}" />
      </div>
      <div style="margin:8px 0;">
        <label for="remote_password"><strong>Password</strong></label>
        <input id="remote_password" name="remote_password" type="password" size="40" value="" />
        <div style="font-size:12px;color:#666;margin-top:4px;">
          For security reasons the current password is not shown. Leave blank to keep the existing one.
        </div>
      </div>
      <div style="margin:8px 0;">
        <label for="remote_path"><strong>Remote path</strong></label>
        <input id="remote_path" name="remote_path" type="text" size="80"
               placeholder="/path/on/server or . for default"
               value="{{ remote_path or '' }}" />
      </div>
      <div style="margin:8px 0;">
        <label>
          <input type="checkbox" id="remote_passive" name="remote_passive"
                 {% if remote_passive %}checked{% endif %} />
          Use passive mode (FTP/FTPS only)
        </label>
      </div>
      <div style="margin:8px 0;">
        <label>
          <input type="checkbox" id="remote_recursive" name="remote_recursive"
                 {% if remote_recursive %}checked{% endif %} />
          Download recursively (include subdirectories)
        </label>
      </div>

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
