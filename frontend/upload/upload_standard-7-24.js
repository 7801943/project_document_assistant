window.initStandardUploadForm = function() {
    // 动态获取表单结构并初始化
    fetch('/api/upload-standards')
        .then(response => {
            if (!response.ok) {
                throw new Error('无法获取表单结构');
            }
            return response.json();
        })
        .then(schema => {
            initializeUploadForm(schema);
        })
        .catch(error => {
            console.error('初始化上传表单失败:', error);
            const errorBox = document.getElementById('upload-error-box');
            if (errorBox) {
                showError(errorBox, '无法加载上传表单，请稍后重试。');
            }
        });
}

function initializeUploadForm(schema) {
    const uploadForm = document.getElementById('upload-form');
    if (!uploadForm) {
        console.error("Upload form not found. Initialization failed.");
        return;
    }

    const categorySelect = document.getElementById('upload-category');
    const filesInput = document.getElementById('upload-files');
    const errorBox = document.getElementById('upload-error-box');
    const progressArea = document.querySelector('.progress-area');
    const progressBar = document.getElementById('upload-progress-bar');
    const progressText = document.getElementById('upload-progress-text');

    // 根据 Schema 填充分类下拉列表
    if (categorySelect && schema.properties.category.enum) {
        schema.properties.category.enum.forEach(category => {
            const option = document.createElement('option');
            option.value = category;
            option.textContent = category;
            categorySelect.appendChild(option);
        });
        if (schema.properties.category.default) {
            categorySelect.value = schema.properties.category.default;
        }
    }

    // 表单重置
    function resetForm() {
        uploadForm.reset();
        errorBox.textContent = '';
        errorBox.style.display = 'none';
        progressArea.style.display = 'none';
        progressBar.style.width = '0%';
        progressText.textContent = '0%';
        filesInput.webkitdirectory = false;
        filesInput.directory = false;
    }

    resetForm(); // 初始化时重置

    // 目录上传切换
    document.querySelectorAll('input[name="upload_type"]').forEach(radio => {
        radio.addEventListener('change', (e) => {
            const isDirectory = e.target.value === 'directory';
            filesInput.webkitdirectory = isDirectory;
            filesInput.directory = isDirectory;
        });
    });

    // 监听文件选择，自动填充规程名称
    filesInput.addEventListener('change', () => {
        if (filesInput.files.length > 0) {
            // 从第一个文件名中提取基本名称（不含扩展名）
            const fileName = filesInput.files[0].name;
            const specName = fileName.includes('.') ? fileName.substring(0, fileName.lastIndexOf('.')) : fileName;

            // 查找或创建隐藏的 spec_name 输入框
            let specNameInput = uploadForm.querySelector('input[name="spec_name"]');
            if (!specNameInput) {
                specNameInput = document.createElement('input');
                specNameInput.type = 'hidden';
                specNameInput.name = 'spec_name';
                uploadForm.appendChild(specNameInput);
            }
            specNameInput.value = specName;
            console.log(`自动设置规程名称为: ${specName}`);
        }
    });

    // 表单提交
    uploadForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        errorBox.textContent = '';
        errorBox.style.display = 'none';

        if (filesInput.files.length === 0) {
            showError(errorBox, '请至少选择一个文件或目录。');
            return;
        }

        const formData = new FormData(uploadForm);

        // 后端不再需要 file_paths，因为文件名在 UploadFile 对象中
        // 但如果前端需要显示，可以保留

        sendUploadRequest(formData);
    });

    function sendUploadRequest(formData) {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', '/api/upload-standards', true);

        xhr.upload.onprogress = function(event) {
            if (event.lengthComputable) {
                const percentComplete = Math.round((event.loaded / event.total) * 100);
                progressArea.style.display = 'block';
                progressBar.style.width = percentComplete + '%';
                progressText.textContent = percentComplete + '%';
            }
        };

        xhr.onload = function() {
            if (xhr.status >= 200 && xhr.status < 300) {
                const result = JSON.parse(xhr.responseText);
                showSuccess(result.message || '上传成功！');
                resetForm();
                if (window.fetchAndRenderFiles) {
                    window.fetchAndRenderFiles();
                }
                setTimeout(() => {
                    if (window.hideUploadModal) window.hideUploadModal();
                }, 1500);
            } else if (xhr.status === 409) {
                const result = JSON.parse(xhr.responseText);
                if (confirm(result.detail + "\n\n是否要覆盖？")) {
                    formData.set('overwrite', 'true');
                    sendUploadRequest(formData); // 重新发送
                } else {
                    showError(errorBox, '上传已取消。');
                }
            } else {
                try {
                    const error = JSON.parse(xhr.responseText);
                    showError(errorBox, error.detail || '上传失败，请检查服务器日志。');
                } catch (e) {
                    showError(errorBox, '上传失败，无法解析服务器响应。');
                }
            }
        };

        xhr.onerror = function() {
            showError(errorBox, '上传过程中发生网络错误。');
        };

        xhr.onloadend = function() {
            setTimeout(() => {
                progressArea.style.display = 'none';
            }, 2000);
        };

        progressArea.style.display = 'block';
        progressBar.style.width = '0%';
        progressText.textContent = '0%';
        xhr.send(formData);
    }
}

function showError(errorBox, message) {
    if (errorBox) {
        errorBox.textContent = message;
        errorBox.style.display = 'block';
    }
}

function showSuccess(message) {
    alert(message); // 简单起见，使用 alert
}
