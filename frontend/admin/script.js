// script.js
document.addEventListener('DOMContentLoaded', () => {
    const menu = document.getElementById('menu');
    const configFormContainer = document.getElementById('config-form-container');
    const currentCategoryTitle = document.getElementById('current-category-title');
    const saveButton = document.getElementById('save-button');

    let allConfigs = {};
    let modelOptions = [];
    let providerPresets = {};
    let activeCategory = '';
    let selectedProvider = null; // 新增：跟踪用户选择的预设

    // 获取配置
    async function fetchConfig() {
        try {
            const response = await fetch('/api/admin_config');
            if (response.status === 404) {
                document.body.innerHTML = '<h1>404 - Not Found</h1><p>您无权访问此页面或页面不存在。</p>';
                return;
            }
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            const data = await response.json();
            allConfigs = data.configs;
            modelOptions = data.model_options || [];
            providerPresets = data.provider_presets || {}; // 新增：获取服务商预设
            renderMenu();
            // 默认显示第一个分类
            if (Object.keys(allConfigs).length > 0) {
                activeCategory = Object.keys(allConfigs)[0];
                renderForm(activeCategory);
                updateActiveMenu();
            }
        } catch (error) {
            console.error('获取配置失败:', error);
            configFormContainer.innerHTML = '<p>加载配置失败，请检查后端服务是否正常。</p>';
        }
    }

    // 渲染侧边栏菜单
    function renderMenu() {
        const menuList = document.createElement('ul');
        for (const category in allConfigs) {
            const menuItem = document.createElement('li');
            menuItem.textContent = category;
            menuItem.dataset.category = category;
            menuItem.addEventListener('click', () => {
                activeCategory = category;
                renderForm(category);
                updateActiveMenu();
            });
            menuList.appendChild(menuItem);
        }
        menu.innerHTML = '';
        menu.appendChild(menuList);
    }

    // 更新菜单激活状态
    function updateActiveMenu() {
        const menuItems = menu.querySelectorAll('li');
        menuItems.forEach(item => {
            if (item.dataset.category === activeCategory) {
                item.classList.add('active');
            } else {
                item.classList.remove('active');
            }
        });
    }

    // 渲染表单
    function renderForm(category) {
        currentCategoryTitle.textContent = category;
        const configs = allConfigs[category];
        let formHtml = `<form id="config-form"><fieldset><legend>${category}</legend>`;
        const sensitiveKeys = ["API_KEY", "APIKEY", "SECRET"];

        // --- 特殊处理：为 "OpenAI 接口" 添加服务商预设下拉列表 ---
        if (category === 'OpenAI 接口' && Object.keys(providerPresets).length > 0) {
            formHtml += `<div class="form-group">`;
            formHtml += `<label for="provider-preset-selector">加载预设</label>`;
            formHtml += `<select id="provider-preset-selector">`;
            formHtml += `<option value="">-- 选择一个服务商 --</option>`;
            for (const providerName in providerPresets) {
                formHtml += `<option value="${providerName}">${providerName}</option>`;
            }
            formHtml += `</select>`;
            formHtml += `</div>`;
        }

        for (const key in configs) {
            const value = configs[key];
            const isSensitive = sensitiveKeys.some(sub => key.toUpperCase().includes(sub));

            formHtml += `<div class="form-group">`;
            formHtml += `<label for="${key}">${key}</label>`;

            // --- 针对 OPENAI_MODEL_NAME 生成可编辑的下拉列表 ---
            if (key === 'OPENAI_MODEL_NAME' && modelOptions.length > 0) {
                formHtml += `<input list="model-options" id="${key}" name="${key}" value="${value}" autocomplete="off">`;
                formHtml += `<datalist id="model-options">`;
                modelOptions.forEach(model => {
                    formHtml += `<option value="${model}">`;
                });
                formHtml += `</datalist>`;
            } else {
                const inputType = getInputType(key, value);
                if (inputType === 'textarea') {
                    formHtml += `<textarea id="${key}" name="${key}">${value}</textarea>`;
                } else if (inputType === 'checkbox') {
                    formHtml += `<input type="checkbox" id="${key}" name="${key}" ${value ? 'checked' : ''}>`;
                } else {
                    // 对敏感字段进行特殊处理
                    if (isSensitive) {
                        const placeholder = value.startsWith('******') ? `已设置，如需修改请输入新值` : '未设置';
                        formHtml += `<input type="${inputType}" id="${key}" name="${key}" value="" placeholder="${placeholder}">`;
                    } else {
                        formHtml += `<input type="${inputType}" id="${key}" name="${key}" value="${value}">`;
                    }
                }
            }
            formHtml += `</div>`;
        }

        formHtml += `</fieldset></form>`;
        configFormContainer.innerHTML = formHtml;

        // --- 为新添加的下拉列表绑定事件 ---
        if (category === 'OpenAI 接口') {
            const presetSelector = document.getElementById('provider-preset-selector');
            if (presetSelector) {
                presetSelector.addEventListener('change', (event) => {
                    selectedProvider = event.target.value; // 更新状态
                    if (selectedProvider && providerPresets[selectedProvider]) {
                        const preset = providerPresets[selectedProvider];
                        
                        // 填充 URL
                        const urlInput = document.getElementById('OPENAI_API_BASE_URL');
                        if (urlInput) {
                            urlInput.value = preset.url;
                        }

                        // 填充 Model Name
                        const modelInput = document.getElementById('OPENAI_MODEL_NAME');
                        if (modelInput && preset.models && preset.models.length > 0) {
                            modelInput.value = preset.models[0]; // 默认使用第一个模型
                        }

                        // 更新 API Key 输入框的提示
                        const keyInput = document.getElementById('OPENAI_API_KEY');
                        if (keyInput) {
                            keyInput.placeholder = preset.has_apikey 
                                ? `已加载预设Key，可输入新值覆盖` 
                                : '未提供预设Key，请输入';
                        }
                    } else {
                        // 如果用户选回了 "-- 选择一个服务商 --"
                        selectedProvider = null;
                    }
                });
            }
        }
    }

    // 根据key和value判断输入框类型
    function getInputType(key, value) {
        if (typeof value === 'boolean') {
            return 'checkbox';
        }
        if (key.toLowerCase().includes('prompt')) {
            return 'textarea';
        }
        if (key.toLowerCase().includes('url')) {
            return 'url';
        }
        if (typeof value === 'number') {
            return 'number';
        }
        return 'text';
    }

    // 保存配置
    async function saveConfig() {
        const form = document.getElementById('config-form');
        if (!form) return;

        const configsToUpdate = {};
        const sensitiveKeys = ["API_KEY", "APIKEY", "SECRET"];

        // 从当前显示的表单中收集数据
        const currentCategoryConfigs = allConfigs[activeCategory];
        for (const key in currentCategoryConfigs) {
            const input = form.elements[key];
            if (input) {
                const isSensitive = sensitiveKeys.some(sub => key.toUpperCase().includes(sub));
                let value;

                if (input.type === 'checkbox') {
                    value = input.checked;
                } else if (input.type === 'number') {
                    value = parseFloat(input.value);
                } else {
                    value = input.value;
                }

                // 对于敏感字段，只有当用户输入了新值时才更新
                if (isSensitive && value === '') {
                    continue; // 跳过未修改的敏感字段
                }
                
                configsToUpdate[key] = value;
            }
        }
        
        // 如果没有需要更新的配置，则直接返回
        if (Object.keys(configsToUpdate).length === 0) {
            showToast('没有需要保存的更改。', 'info');
            return;
        }

        try {
            const payload = {
                configs: configsToUpdate,
                selected_provider: selectedProvider
            };

            const response = await fetch('/api/admin_config', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(payload),
            });

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.detail || '保存失败');
            }

            const result = await response.json();
            showToast(result.message, 'success');

            // 重新获取配置以刷新状态，确保显示的是后端保存的最新值
            fetchConfig();

        } catch (error) {
            console.error('保存配置失败:', error);
            showToast(`保存失败: ${error.message}`, 'error');
        }
    }

    // 显示消息提示
    function showToast(message, type = 'success') {
        const toast = document.createElement('div');
        toast.className = `toast-notification ${type}`;
        toast.textContent = message;
        document.body.appendChild(toast);

        // 触发动画
        setTimeout(() => {
            toast.classList.add('show');
        }, 100);

        // 3秒后自动消失
        setTimeout(() => {
            toast.classList.remove('show');
            setTimeout(() => {
                document.body.removeChild(toast);
            }, 500);
        }, 3000);
    }

    // 绑定事件
    saveButton.addEventListener('click', saveConfig);

    // 初始化
    fetchConfig();
});
