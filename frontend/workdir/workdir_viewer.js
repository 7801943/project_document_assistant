// 将所有逻辑封装在一个立即执行函数表达式中，以避免污染全局作用域
(function(window) {
    'use strict';

    // 定义 WorkdirManager 对象
    const WorkdirManager = {};
    let currentDir = null;
    let treeContainer = null;
    let actionCallbacks = {}; // 存储来自主页面的回调函数

    // 新增：模态框和表单相关的DOM元素
    let createProjectModal, createProjectForm, createProjectBtn, closeModalBtn, cancelModalBtn;
    // 新增：搜索相关的DOM元素
    let searchInput, searchBtn; // 移除 resetSearchBtn, projectSelectorContainer
    let debounceTimer;

    // 新增：项目选择模态框相关的DOM元素
    let projectSelectionModal, projectListContainer, closeProjectSelectionModalBtn;

    /**
     * 初始化搜索功能的DOM元素和事件监听
     */
    function initializeSearch() {
        searchInput = document.getElementById('search-input');
        searchBtn = document.getElementById('search-btn');

        if (!searchInput || !searchBtn) {
            console.error('未能找到搜索功能所需的一个或多个DOM元素。');
            return;
        }

        searchBtn.addEventListener('click', () => {
            clearTimeout(debounceTimer); // 点击按钮立即触发搜索
            handleSearch();
        });

        searchInput.addEventListener('input', () => {
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(() => {
                handleSearch();
            }, 3000); // 3秒防抖
        });

        // 新增：监听回车键，触发搜索
        searchInput.addEventListener('keydown', (event) => {
            if (event.key === 'Enter') {
                event.preventDefault(); // 阻止默认的回车行为（如表单提交）
                clearTimeout(debounceTimer); // 清除任何待处理的防抖计时器
                handleSearch(); // 立即执行搜索
            }
        });

        // 初始化项目选择模态框的DOM元素和事件
        projectSelectionModal = document.getElementById('project-selection-modal');
        projectListContainer = document.getElementById('project-list-container');
        closeProjectSelectionModalBtn = projectSelectionModal.querySelector('.modal-close-button');

        if (projectSelectionModal && closeProjectSelectionModalBtn) {
            closeProjectSelectionModalBtn.addEventListener('click', () => {
                projectSelectionModal.style.display = 'none';
            });
            window.addEventListener('click', (event) => {
                if (event.target == projectSelectionModal) {
                    projectSelectionModal.style.display = 'none';
                }
            });
        }
    }

    /**
     * 处理文件搜索
     */
    async function handleSearch() {
        const project_name_query = searchInput.value.trim();
        // 假设 project_year_query 暂时为空，如果需要，前端需要添加输入字段
        const project_year_query = null; // 或者从新的输入字段获取

        if (!project_name_query) {
            alert("项目名称不能为空。");
            return;
        }

        try {
            const requestBody = {
                project_name: project_name_query
            };
            if (project_year_query) {
                requestBody.project_year = project_year_query;
            }

            const response = await fetch('/api/projects/search', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(requestBody)
            });

            if (!response.ok) {
                const errorResult = await response.json();
                throw new Error(errorResult.detail || '搜索失败');
            }

            const result = await response.json();

            if (result.status === "multiple_projects") {
                // 多个项目，弹出模态框让用户选择
                showProjectSelectionModal(result.projects);
            } else if (result.status === "no_project_found") {
                // 没有找到项目，可以清空文件树或显示提示
                //WorkdirManager.updateData([]); // 清空树状图
                alert("未找到相关项目。");
            } else if (result.status === "single_project") {
                // 找到一个项目，后端已通过WebSocket发送目录更新指令，前端无需处理
                console.log("后端已处理搜索请求并发送目录更新指令，找到一个项目。");
            } else {
                // 其他情况，例如后端返回了未知状态或错误
                console.warn("后端返回了未知状态或错误:", result);
                alert("搜索结果异常，请联系管理员。");
            }
        } catch (error) {
            console.error('搜索文件时出错:', error);
            alert(`错误: ${error.message}`);
        }
    }

    /**
     * 显示项目选择模态框
     * @param {Array} projects - 项目列表
     */
    function showProjectSelectionModal(projects) {
        console.log("showProjectSelectionModal。");

        if (!projectSelectionModal || !projectListContainer) {
            console.error('项目选择模态框DOM元素未找到。');
            return;
        }

        projectListContainer.innerHTML = ''; // 清空旧内容

        projects.forEach(project => {
            const projectItemBtn = document.createElement('button');
            projectItemBtn.className = 'project-item-btn';
            projectItemBtn.textContent = `${project.year} - ${project.project_name}`; // 直接显示 year 和 project_name
            projectItemBtn.addEventListener('click', () => {
                fetchFilesForProject({
                    year: project.year,
                    project_name: project.project_name,
                    type: project.type // type 参数虽然后端不再使用，但前端为了兼容旧数据结构可以保留
                });
                projectSelectionModal.style.display = 'none'; // 关闭模态框
            });
            projectListContainer.appendChild(projectItemBtn);
        });

        projectSelectionModal.style.display = 'flex'; // 显示模态框
    }

    /**
     * 获取特定项目的文件列表
     * @param {object} projectDetails - 包含 year, project_name, type 的对象
     */
    async function fetchFilesForProject(projectDetails) {
        try {
            const response = await fetch('/api/projects/search', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    project_year: projectDetails.year,
                    project_name: projectDetails.project_name
                })
            });

            if (!response.ok) {
                const errorResult = await response.json();
                throw new Error(errorResult.detail || '获取项目文件失败');
            }
            // 后端会通过WebSocket发送目录更新指令，前端无需再操作文件树
            console.log("已请求特定项目文件，后端将通过WebSocket发送目录更新指令。");
        } catch (error) {
            console.error('获取特定项目文件时出错:', error);
            alert(`错误: ${error.message}`);
        }
    }


    /**
     * 初始化创建项目模态框的DOM元素和事件监听
     */
    function initializeModal() {
        createProjectModal = document.getElementById('create-project-modal');
        createProjectForm = document.getElementById('create-project-form');
        createProjectBtn = document.getElementById('create-project-btn');
        closeModalBtn = createProjectModal.querySelector('.modal-close-button');
        cancelModalBtn = document.getElementById('cancel-create-project');

        if (!createProjectModal || !createProjectForm || !createProjectBtn || !closeModalBtn || !cancelModalBtn) {
            console.error('未能找到创建项目模态框所需的一个或多个DOM元素。');
            return;
        }

        // 动态填充年份下拉列表
        const yearSelect = document.getElementById('project-year');
        if (yearSelect) {
            const currentYear = new Date().getFullYear();
            for (let year = 2030; year >= 2010; year--) {
                const option = document.createElement('option');
                option.value = year;
                option.textContent = year;
                if (year === currentYear) {
                    option.selected = true;
                }
                yearSelect.appendChild(option);
            }
        }

        createProjectBtn.addEventListener('click', () => {
            createProjectModal.style.display = 'flex';
        });

        closeModalBtn.addEventListener('click', () => {
            createProjectModal.style.display = 'none';
        });

        cancelModalBtn.addEventListener('click', () => {
            createProjectModal.style.display = 'none';
        });

        window.addEventListener('click', (event) => {
            if (event.target == createProjectModal) {
                createProjectModal.style.display = 'none';
            }
        });

        createProjectForm.addEventListener('submit', handleProjectCreate);

        // 新增：监听文件选择变化，自动填充项目名称
        const projectFilesInput = document.getElementById('project-files');
        const projectNameInput = document.getElementById('project-name');
        if (projectFilesInput && projectNameInput) {
            projectFilesInput.addEventListener('change', () => {
                if (projectFilesInput.files.length > 0) {
                    // webkitRelativePath 的格式是 "目录名/文件名.ext"
                    const firstFilePath = projectFilesInput.files[0].webkitRelativePath;
                    if (firstFilePath) {
                        // 提取第一个路径部分作为项目名
                        const projectName = firstFilePath.split('/')[0];
                        projectNameInput.value = projectName;
                    }
                }
            });
        }
    }

    /**
     * 处理项目创建表单的提交
     * @param {Event} event
     */
    async function handleProjectCreate(event) {
        event.preventDefault();
        
        const year = document.getElementById('project-year').value;
        const projectName = document.getElementById('project-name').value.trim();
        const files = document.getElementById('project-files').files;

        if (!projectName) {
            alert('请输入项目名称。');
            return;
        }
        if (files.length === 0) {
            alert('请选择要上传的目录。');
            return;
        }

        let overwrite = false;

        // 1. 检查项目是否存在
        try {
            const checkResponse = await fetch(`/api/upload-project?year_query=${year}&project_name_query=${encodeURIComponent(projectName)}`, {
                method: 'GET',
            });

            // 检查响应是否成功，如果不成功，先尝试解析JSON获取错误信息
            if (!checkResponse.ok) {
                const errorResult = await checkResponse.json().catch(() => null);
                // 特别处理 409 Conflict
                if (checkResponse.status === 409 && errorResult) {
                    if (!confirm(errorResult.message || `项目 "${projectName}" 已存在。您想合并文件并覆盖现有内容吗？`)) {
                        alert('上传已取消。');
                        return; // 用户取消操作
                    }
                    overwrite = true;
                } else {
                    // 其他所有HTTP错误
                    throw new Error(errorResult ? errorResult.message : `HTTP 错误! 状态: ${checkResponse.status}`);
                }
            } else {
                 // 响应成功 (status 200)
                const result = await checkResponse.json();
                if (result.status === 'not_exists') {
                    // 项目不存在，可以安全上传
                    overwrite = false;
                } else {
                    // 收到200但内容不是预期的 not_exists
                    console.warn("收到了意外的成功响应:", result);
                }
            }
        } catch (error) {
            console.error('检查项目是否存在时出错:', error);
            alert(`错误: ${error.message}`);
            return;
        }

        // 2. 准备并发送上传请求
        const formData = new FormData();
        formData.append('year', year);
        formData.append('project_name', projectName);
        formData.append('overwrite', overwrite);

        for (let i = 0; i < files.length; i++) {
            // 使用 webkitRelativePath 来保留目录结构
            const path = files[i].webkitRelativePath || files[i].name;
            formData.append('files', files[i], path);
        }

        try {
            const uploadResponse = await fetch('/api/upload-project', {
                method: 'POST',
                body: formData,
            });

            const result = await uploadResponse.json();

            if (uploadResponse.ok) {
                alert('项目上传成功！');
                createProjectModal.style.display = 'none';
                createProjectForm.reset();
                // 刷新工作目录视图
                if (actionCallbacks.refresh) {
                    actionCallbacks.refresh();
                } else {
                    console.log("需要一个方法来刷新工作目录视图");
                }
            } else {
                throw new Error(result.detail || '上传项目失败');
            }
        } catch (error) {
            console.error('上传项目时出错:', error);
            alert(`错误: ${error.message}`);
        }
    }


    /**
     * 初始化文件树查看器
     * @param {string} containerSelector - The CSS selector for the container element.
     * @param {object} callbacks - An object containing action callbacks from the parent.
     */
    WorkdirManager.initialize = function(containerSelector, callbacks) {
        treeContainer = $(containerSelector);
        actionCallbacks = callbacks || {};

        if (treeContainer.length === 0) {
            console.error('Workdir viewer container not found:', containerSelector);
            return;
        }

        treeContainer.jstree({
            'core': {
                'data': [],
                'check_callback': true,
                'themes': {
                    'name': 'default',
                    'responsive': true,
                    'stripes': true
                }
            },
            'plugins': ['contextmenu', 'types','wholerow'],
            'types': {
                'default': { 'icon': 'jstree-icon jstree-file' },
                'folder': { 'icon': 'jstree-icon jstree-folder' }
            },
            'contextmenu': {
                'items': function(node) {
                    const items = {};
                    if (node.type === 'default') { // 文件节点
                        items.open = {
                            'label': '打开',
                            'icon': 'fas fa-folder-open',
                            'action': () => {
                                if (actionCallbacks.open) {
                                    actionCallbacks.open(node.data);
                                }
                            }
                        };
                        if (['docx', 'xlsx'].includes(node.data.format) && actionCallbacks.edit) {
                            items.edit = {
                                'label': '编辑',
                                'icon': 'fas fa-edit',
                                'action': () => {
                                    actionCallbacks.edit(node.data.file_path, node.data.download_token);
                                }
                            };
                        }
                        if (actionCallbacks.llmRead) {
                            items.llmRead = {
                                'label': 'LLM读取',
                                'icon': 'fas fa-robot',
                                'action': () => {
                                    actionCallbacks.llmRead(node.data.file_path);
                                }
                            };
                        }
                    } else if (node.type === 'folder') { // 文件夹节点
                        items.uploadDirectory = {
                            'label': '上传目录',
                            'icon': 'fas fa-folder-upload', // 目录上传图标
                            'action': () => {
                                handleUpload(node.id, true); // true 表示上传目录
                            }
                        };
                        items.uploadFiles = {
                            'label': '上传文件',
                            'icon': 'fas fa-file-upload', // 文件上传图标
                            'action': () => {
                                handleUpload(node.id, false); // false 表示上传文件
                            }
                        };
                    }
                    return items;
                }
            }
        });

        // 统一处理文件和目录上传的函数
        async function handleUpload(targetFolderPath, isDirectoryUpload) {
            const fileInput = document.createElement('input');
            fileInput.type = 'file';
            fileInput.multiple = true; // 总是允许多选

            if (isDirectoryUpload) {
                fileInput.webkitdirectory = true; // 允许选择目录
            } else {
                // 如果是文件上传，不设置 webkitdirectory
                // fileInput.accept = ".doc,.docx,.pdf"; // 可以限制文件类型
            }

            fileInput.addEventListener('change', async (event) => {
                const files = event.target.files;
                if (files.length === 0) {
                    alert('未选择任何文件或目录。');
                    return;
                }

                let overwrite = false;

                // 1. 检查目标目录是否存在
                try {
                    const checkResponse = await fetch(`/api/upload-files?relative_path=${encodeURIComponent(targetFolderPath)}`, {
                        method: 'GET',
                    });

                    if (checkResponse.status === 200) {
                        // 目录存在，询问用户是否覆盖
                        if (!confirm(`目录 "${targetFolderPath}" 已存在。您想合并文件并覆盖现有内容吗？`)) {
                            alert('上传已取消。');
                            return; // 用户取消操作
                        }
                        overwrite = true;
                    } else if (checkResponse.status !== 404) {
                        // 如果不是“不存在”，则可能是一个未预料的错误
                        const errorResult = await checkResponse.json().catch(() => ({ detail: '检查目录是否存在时发生未知错误。' }));
                        throw new Error(errorResult.detail);
                    }
                    // 如果是404，则目录不存在，正常继续

                } catch (error) {
                    console.error('检查目录是否存在时出错:', error);
                    alert(`错误: ${error.message}`);
                    return;
                }

                // 2. 准备并发送上传请求
                const formData = new FormData();
                formData.append('relative_path', targetFolderPath);
                formData.append('overwrite', overwrite);
                // 增加爱项目名称字段
                formData.append('project_name',currentDir)
                console.log("上传数据",formData)
                for (let i = 0; i < files.length; i++) {
                    // 对于目录上传，使用 webkitRelativePath 保留目录结构
                    // 对于文件上传，file.name 即可
                    const path = isDirectoryUpload ? files[i].webkitRelativePath : files[i].name;
                    formData.append('files', files[i], path);
                }

                try {
                    const uploadResponse = await fetch('/api/upload-files', {
                        method: 'POST',
                        body: formData,
                    });

                    const result = await uploadResponse.json();

                    if (uploadResponse.ok) {
                        alert('文件上传成功！');
                        // 后端会通过 WebSocket 刷新文件树，前端无需额外操作
                    } else {
                        throw new Error(result.detail || '上传文件失败');
                    }
                } catch (error) {
                    console.error('上传文件时出错:', error);
                    alert(`错误: ${error.message}`);
                }
            });

            fileInput.click(); // 触发文件选择对话框
        }

        treeContainer.one('ready.jstree', function () {
            treeContainer.jstree(true).open_all();
        });

        initializeModal();
        initializeSearch();
    };

    /**
     * 更新文件树数据
     * @param {Array} files - The flat list of file objects.
     * @param {Object} dir
     */
    WorkdirManager.updateData = function(dir) {
        if (!treeContainer || !treeContainer.jstree(true)) {
            console.error('jsTree instance not initialized.');
            return;
        }
        currentDir = dir;
        const treeData = buildTreeData(dir.files);
        treeContainer.jstree(true).settings.core.data = treeData;
        treeContainer.jstree(true).refresh();
    };

    /**
     * 更新节点状态（例如，已打开的文件）
     * @param {Object} openFiles - An object with open file paths as keys.
     */
    WorkdirManager.updateNodeStates = function(openFiles) {
        const treeInstance = treeContainer.jstree(true);
        if (!treeInstance) return;

        const allNodes = treeInstance.get_json('#', { flat: true });
        allNodes.forEach(node => {
            const icon = node.type === 'folder' ? 'jstree-icon jstree-folder' : 'jstree-icon jstree-file';
            let finalIcon = icon;

            if (node.data && node.data.file_path && openFiles[node.data.file_path]) {
                // 如果文件已打开，使用一个不同的图标来表示
                // 例如，可以使用 Font Awesome 的 'fa-file-alt' 或 'fa-file-code' 等
                // 确保在 index7.html 中引入了 Font Awesome
                finalIcon = 'fas fa-file-alt'; // 示例：使用一个不同的文件图标
            }
            // 更新节点的图标
            treeInstance.set_icon(node.id, finalIcon);
        });
    };


    // 将扁平的文件列表转换为 jsTree 的层级结构
    function buildTreeData(files) {
        const tree = [];
        const map = {};

        if (!files) return tree;

        files.forEach(file => {
            const pathParts = file.file_path.split('/');
            let currentLevel = tree;
            let currentPath = '';

            pathParts.forEach((part, index) => {
                const isFile = index === pathParts.length - 1;
                currentPath = currentPath ? `${currentPath}/${part}` : part;
                let node = map[currentPath];

                if (!node) {
                    node = {
                        id: currentPath,
                        text: part,
                        type: isFile ? 'default' : 'folder',
                        children: isFile ? null : [],
                        data: isFile ? file : null,
                        state: { opened: !isFile }
                    };
                    map[currentPath] = node;
                    currentLevel.push(node);
                }

                if (!isFile) {
                    if (!node.children) {
                        node.children = [];
                    }
                    currentLevel = node.children;
                }
            });
        });
        return tree;
    }

    window.WorkdirManager = WorkdirManager;

})(window);
