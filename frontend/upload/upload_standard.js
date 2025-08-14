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

    // --- DOM 元素获取 ---
    const categorySelect = document.getElementById('upload-category');
    const filesInput = document.getElementById('upload-files');
    const fileListContainer = document.getElementById('file-list-container');
    const errorBox = document.getElementById('upload-error-box');
    const progressArea = document.querySelector('.progress-area');
    const progressBar = document.getElementById('upload-progress-bar');
    const progressText = document.getElementById('upload-progress-text');

    // --- 状态变量 ---
    let uploadQueue = []; // 存储待上传的文件/目录对象

    // --- 初始化 ---
    // 根据 Schema 填充分类下拉列表
    if (categorySelect && schema.properties.category.enum) {
        categorySelect.innerHTML = '<option value="" disabled>请选择专业</option>'; // 清空并添加默认
        schema.properties.category.enum.forEach(category => {
            const option = document.createElement('option');
            option.value = category;
            option.textContent = category;
            categorySelect.appendChild(option);
        });
        if (schema.properties.category.default) {
            categorySelect.value = schema.properties.category.default;
        } else if (schema.properties.category.enum.length > 0) {
            categorySelect.value = schema.properties.category.enum[0];
        }
    }

    // --- 函数定义 ---

    // 表单重置
    function resetForm() {
        uploadForm.reset();
        uploadQueue = [];
        renderFileList();
        errorBox.textContent = '';
        errorBox.style.display = 'none';
        progressArea.style.display = 'none';
        progressBar.style.width = '0%';
        progressText.textContent = '0%';
        filesInput.value = ''; // 清空文件选择
        filesInput.webkitdirectory = false;
        filesInput.directory = false;
        // 确保切换回文件上传模式
        const fileRadio = document.getElementById('upload-type-file');
        if(fileRadio) fileRadio.checked = true;
    }

    // 渲染待上传列表
    function renderFileList() {
        fileListContainer.innerHTML = '';
        if (uploadQueue.length === 0) {
            fileListContainer.innerHTML = '<p style="color: var(--text-secondary); text-align: center;">请选择文件或目录以上传</p>';
            return;
        }

        uploadQueue.forEach((item, index) => {
            const fileItem = document.createElement('div');
            fileItem.className = 'file-list-item';
            fileItem.style.cssText = `
                display: flex;
                justify-content: space-between;
                align-items: center;
                padding: 8px;
                border-bottom: 1px solid var(--border-color);
                font-size: 0.9em;
            `;

            const fileName = document.createElement('span');
            fileName.textContent = item.name;
            fileName.style.whiteSpace = 'nowrap';
            fileName.style.overflow = 'hidden';
            fileName.style.textOverflow = 'ellipsis';

            const removeBtn = document.createElement('button');
            removeBtn.textContent = '删除';
            removeBtn.className = 'auth-button login';
            removeBtn.style.cssText = 'padding: 4px 8px; font-size: 0.8em; margin-left: 10px;';
            removeBtn.onclick = () => {
                uploadQueue.splice(index, 1);
                renderFileList();
            };

            fileItem.appendChild(fileName);
            fileItem.appendChild(removeBtn);
            fileListContainer.appendChild(fileItem);
        });
    }

    // 处理文件选择
    function handleFileSelection() {
        const selectedFiles = Array.from(filesInput.files);
        const isDirectoryUpload = filesInput.webkitdirectory || filesInput.directory;

        if (isDirectoryUpload) {
            // 目录上传，将整个目录视为一个上传项
            if (selectedFiles.length > 0) {
                const directoryName = selectedFiles[0].webkitRelativePath.split('/')[0];
                uploadQueue.push({
                    id: `dir-${Date.now()}`,
                    name: directoryName,
                    files: selectedFiles,
                    type: 'directory'
                });
            }
        } else {
            // 文件上传，每个文件都是一个独立的上传项
            selectedFiles.forEach(file => {
                uploadQueue.push({
                    id: `file-${Date.now()}-${Math.random()}`,
                    name: file.name,
                    files: [file],
                    type: 'file'
                });
            });
        }
        renderFileList();
        filesInput.value = ''; // 清空以便再次选择相同文件
    }

    // 发起单个上传请求
    function sendUploadRequest(item, category, overwrite) {
        return new Promise((resolve, reject) => {
            const formData = new FormData();
            const specName = item.name.includes('.') ? item.name.substring(0, item.name.lastIndexOf('.')) : item.name;

            formData.append('category', category);
            formData.append('spec_name', specName);
            formData.append('overwrite', overwrite);
            item.files.forEach(file => {
                formData.append('files', file, file.webkitRelativePath || file.name);
            });

            const xhr = new XMLHttpRequest();
            xhr.open('POST', '/api/upload-standards', true);

            xhr.upload.onprogress = function(event) {
                if (event.lengthComputable) {
                    // 这里可以更新单个项目的进度条（如果实现的话）
                }
            };

            xhr.onload = function() {
                if (xhr.status >= 200 && xhr.status < 300) {
                    resolve(JSON.parse(xhr.responseText));
                } else {
                    try {
                        const error = JSON.parse(xhr.responseText);
                        reject({ item: item, error: error.detail || '上传失败' });
                    } catch (e) {
                        reject({ item: item, error: '无法解析服务器响应' });
                    }
                }
            };

            xhr.onerror = function() {
                reject({ item: item, error: '网络错误' });
            };

            xhr.send(formData);
        });
    }

    // --- 事件监听 ---

    // 初始调用
    resetForm();
    renderFileList();

    // 目录/文件上传类型切换
    document.querySelectorAll('input[name="upload_type"]').forEach(radio => {
        radio.addEventListener('change', (e) => {
            const isDirectory = e.target.value === 'directory';
            filesInput.webkitdirectory = isDirectory;
            filesInput.directory = isDirectory;
        });
    });

    // 监听文件选择
    filesInput.addEventListener('change', handleFileSelection);

    // 表单提交
    uploadForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        errorBox.textContent = '';
        errorBox.style.display = 'none';

        if (uploadQueue.length === 0) {
            showError(errorBox, '请至少选择一个文件或目录。');
            return;
        }

        const category = categorySelect.value;
        if (!category) {
            showError(errorBox, '请选择一个专业分类。');
            return;
        }
        const overwrite = document.getElementById('upload-overwrite').checked;

        progressArea.style.display = 'block';
        let successCount = 0;
        let errorCount = 0;
        const totalTasks = uploadQueue.length;

        const uploadPromises = uploadQueue.map(item =>
            sendUploadRequest(item, category, overwrite)
                .then(result => {
                    successCount++;
                    console.log(`上传成功: ${item.name}`, result);
                })
                .catch(errorInfo => {
                    errorCount++;
                    console.error(`上传失败: ${errorInfo.item.name}`, errorInfo.error);
                    // 可以在这里更新UI，标记失败的项目
                    showError(errorBox, `文件 '${errorInfo.item.name}' 上传失败: ${errorInfo.error}`, true);
                })
                .finally(() => {
                    const percentComplete = Math.round(((successCount + errorCount) / totalTasks) * 100);
                    progressBar.style.width = percentComplete + '%';
                    progressText.textContent = `${percentComplete}% (${successCount + errorCount}/${totalTasks})`;
                })
        );

        await Promise.all(uploadPromises);

        // 全部完成后处理
        if (errorCount === 0) {
            showSuccess(`全部 ${successCount} 个任务上传成功！`);
            if (window.fetchAndRenderFiles) {
                window.fetchAndRenderFiles();
            }
            setTimeout(() => {
                if (window.hideUploadModal) window.hideUploadModal();
                resetForm();
            }, 1500);
        } else {
            showError(errorBox, `上传完成: ${successCount} 个成功, ${errorCount} 个失败。请检查列表和控制台日志。`, false);
        }

        // 清空队列并重置
        uploadQueue = [];
        renderFileList();
    });
}

function showError(errorBox, message, append = false) {
    if (errorBox) {
        if (append) {
            const p = document.createElement('p');
            p.textContent = message;
            errorBox.appendChild(p);
        } else {
            errorBox.innerHTML = ''; // 清空旧消息
            const p = document.createElement('p');
            p.textContent = message;
            errorBox.appendChild(p);
        }
        errorBox.style.display = 'block';
    }
}

function showSuccess(message) {
    alert(message); // 简单起见，使用 alert
}
