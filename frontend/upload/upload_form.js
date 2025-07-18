function initializeUploadForm(categories) {
    // 确保只在 upload_form.html 被加载后执行
    const uploadForm = document.getElementById('upload-form');
    if (!uploadForm) {
        console.error("Upload form not found in the DOM. Initialization failed.");
        return;
    }

    const title = document.getElementById('upload-modal-title');
    const specFields = document.getElementById('spec-fields');
    const projectFields = document.getElementById('project-fields');
    const categorySelect = document.getElementById('upload-category');
    const yearInput = document.getElementById('upload-year');
    const filesInput = document.getElementById('upload-files');
    const errorBox = document.getElementById('upload-error-box');
    const progressArea = document.querySelector('.progress-area');
    const progressBar = document.getElementById('upload-progress-bar');
    const progressText = document.getElementById('upload-progress-text');

    // 填充专业类别
    if (categorySelect && categories && categories.length > 0) {
        categories.forEach(category => {
            const option = document.createElement('option');
            option.value = category;
            option.textContent = category;
            categorySelect.appendChild(option);
        });
    }

    // 切换上传目标
    document.querySelectorAll('input[name="upload_target"]').forEach(radio => {
        radio.addEventListener('change', (e) => {
            toggleTargetFields(e.target.value);
        });
    });

    function toggleTargetFields(target) {
        if (target === 'spec') {
            title.textContent = '上传规程文件或目录';
            specFields.style.display = 'block';
            projectFields.style.display = 'none';
            specFields.disabled = false;
            projectFields.disabled = true;
        } else { // project
            title.textContent = '上传项目文件或目录';
            specFields.style.display = 'none';
            projectFields.style.display = 'block';
            specFields.disabled = true;
            projectFields.disabled = false;
        }
    }

    // 初始化时根据默认选中的目标来切换字段
    const initialTarget = document.querySelector('input[name="upload_target"]:checked').value;
    toggleTargetFields(initialTarget);

    // 目录上传时，设置 webkitdirectory 属性
    document.querySelectorAll('input[name="upload_type"]').forEach(radio => {
        radio.addEventListener('change', (e) => {
            const isDirectory = e.target.value === 'directory';
            filesInput.webkitdirectory = isDirectory;
            filesInput.directory = isDirectory; // 增加兼容性
        });
    });
    // 初始化
    const isDirectoryInitially = document.querySelector('input[name="upload_type"]:checked').value === 'directory';
    filesInput.webkitdirectory = isDirectoryInitially;
    filesInput.directory = isDirectoryInitially;


    // 表单提交处理
    uploadForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        errorBox.textContent = '';
        errorBox.style.display = 'none';

        const uploadTarget = document.querySelector('input[name="upload_target"]:checked').value;
        const files = filesInput.files;

        // 1. 前端校验
        if (files.length === 0) {
            showError('请至少选择一个文件或目录。');
            return;
        }

        // 1.1. 项目上传时，检查压缩文件
        if (uploadTarget === 'project') {
            const compressedFileExtensions = ['.zip', '.rar', '.7z', '.tar', '.gz', '.bz2'];
            for (const file of files) {
                const fileName = file.name.toLowerCase();
                if (compressedFileExtensions.some(ext => fileName.endsWith(ext))) {
                    showError(`不允许上传压缩文件: ${file.name}`);
                    if (!confirm(`检测到压缩文件 "${file.name}"。确定要继续上传吗？`)) {
                        return;
                    }
                }
            }
        }

        // 2. 构建 FormData
        const formData = new FormData(uploadForm);

        // 2.2. 添加文件路径
        for (const file of files) {
            formData.append('file_paths', file.webkitRelativePath || file.name);
        }

        // 3. 发送请求
        await sendUploadRequest(formData);
    });

    async function sendUploadRequest(formData, isRetry = false) {
        progressArea.style.display = 'block';
        progressBar.style.width = '0%';
        progressText.textContent = '0%';

        try {
            const response = await fetch('/upload-directory/', {
                method: 'POST',
                body: formData,
                // fetch with FormData does not need Content-Type header, browser sets it with boundary
            });

            if (response.ok) {
                const result = await response.json();
                showSuccess(result.message || '上传成功！');
                // 可以在这里触发文件列表刷新
                if (window.fetchAndRenderFiles) {
                    window.fetchAndRenderFiles();
                }
                setTimeout(() => {
                    // Use the global function to hide the modal
                    if (window.hideUploadModal) {
                        window.hideUploadModal();
                    } else {
                        // Fallback if global function is not available
                        const closeButton = document.getElementById('close-upload-modal-button');
                        if(closeButton) closeButton.click();
                    }
                }, 1500);
            } else if (response.status === 409) { // 冲突
                const result = await response.json();
                if (confirm(result.message + "\n\n是否要覆盖已存在的文件/目录？")) {
                    formData.set('overwrite', 'true'); // 确认覆盖
                    await sendUploadRequest(formData, true); // 重新发送请求
                } else {
                    showError('上传已取消。');
                }
            } else {
                const error = await response.json();
                showError(error.detail || '上传失败，请检查服务器日志。');
            }
        } catch (error) {
            console.error('Upload error:', error);
            showError('上传过程中发生网络错误。');
        } finally {
            if (!isRetry) { // 避免在重试的确认框弹出时隐藏进度条
                 setTimeout(() => {
                    progressArea.style.display = 'none';
                }, 2000);
            }
        }
    }

    function showError(message) {
        errorBox.textContent = message;
        errorBox.style.display = 'block';
    }

    function showSuccess(message) {
        // 可以用一个更友好的提示方式，例如一个短暂的成功消息条
        alert(message);
    }

    // 自动填充项目名称和规程名称
    filesInput.addEventListener('change', () => {
        const uploadTarget = document.querySelector('input[name="upload_target"]:checked').value;
        const uploadType = document.querySelector('input[name="upload_type"]:checked').value;
        const firstFile = filesInput.files[0];

        if (!firstFile) {
            console.warn('未选择任何文件');
            return;
        }

        let directoryName = null;

        if (uploadType === 'directory' && firstFile.webkitRelativePath) {
            // 正常目录上传
            directoryName = firstFile.webkitRelativePath.split('/')[0];
        } else {
            // ⚠️ 文件上传：提取文件名（不含扩展名）
            const fileName = firstFile.name;
            const lastDotIndex = fileName.lastIndexOf('.');
            directoryName = lastDotIndex > 0 ? fileName.slice(0, lastDotIndex) : fileName;
        }

        console.log('📝 提取目录名:', directoryName);

        if (directoryName) {
            if (uploadTarget === 'project') {
                const projectNameInput = document.getElementById('upload-project-name');
                if (projectNameInput && !projectNameInput.value) {
                    projectNameInput.value = directoryName;
                }
            } else if (uploadTarget === 'spec') {
                const specNameInput = document.getElementById('upload-spec-name');
                if (specNameInput && !specNameInput.value) {
                    specNameInput.value = directoryName;
                }
            }
        }
    });

}
