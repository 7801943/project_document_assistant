// --- Chat Component Script ---
// This script assumes that global variables like `chatSocket`, `current_conversation_id`, etc.
// and functions like `handleChatWebSocketDisconnect` are defined in the main page (index7.html).

// We will initialize the DOM elements once the script is loaded.
let dynamic_bot_api_url = null;
let dynamic_bot_api_key = null;

// These are global variables defined in index7.html.
// This script will assign values to them after the DOM is loaded.

// botui.html's theme toggle JS logic is removed. index4.html's setTheme handles global theme.

function startNewConversation() {
    // 检查是否有历史消息，如果有，则提示用户
    if (botui_messageWindow.children.length > 1) { // 假设初始有一条欢迎消息
        const confirmNewChat = confirm("开始新对话将清除当前对话内容。您确定要开始新对话吗？");
        if (!confirmNewChat) {
            return; // 用户取消，不执行新对话逻辑
        }
    }

    current_conversation_id = generateUUID(); // 生成新的对话ID
    botui_messageWindow.innerHTML = '';
    botui_addMessage("新对话已开始。请输入您的问题。", "bot");
    console.log("New conversation started. New Conversation ID:", current_conversation_id);

    // 诊断日志
    console.log("准备发送 start_conversation 事件，检查 WebSocket 状态...");
    console.log("chatSocket 对象:", chatSocket);
    if(chatSocket) {
        console.log("chatSocket.readyState:", chatSocket.readyState, "(0=CONNECTING, 1=OPEN, 2=CLOSING, 3=CLOSED)");
    }

    // Bug 1 修复：通知后端新对话已开始
    if (chatSocket && chatSocket.readyState === WebSocket.OPEN) {
        chatSocket.send(JSON.stringify({
            type: "start_conversation",
            conversation_id: current_conversation_id
        }));
        console.log("Sent start_conversation event to backend.");
    } else {
        console.warn("Could not send start_conversation event: WebSocket not open.");
    }

    botui_chatInput.focus();
}

function initializeChatDOMElements() {
    botui_messageWindow = document.getElementById('message-window');
    botui_chatForm = document.getElementById('chat-form');
    botui_chatInput = document.getElementById('chat-input');
    botui_sendButton = document.getElementById('send-button');
    botui_stopAIButton = document.getElementById('stop-ai-button');
    botui_newChatButton = document.getElementById('new-chat-button');

    // Attach event listeners now that the elements are available
    // 直接调用 startNewConversation，其中包含了确认逻辑
    botui_newChatButton.addEventListener('click', startNewConversation);
    botui_chatForm.addEventListener('submit', (event) => {
        event.preventDefault();
        botui_handleFormSubmission();
    });
    botui_stopAIButton.addEventListener('click', () => {
        if (current_task_id && chatSocket && chatSocket.readyState === WebSocket.OPEN) {
            console.log(`Requesting to stop AI task: ${current_task_id}`);
            isStreaming = false;
            chatSocket.send(JSON.stringify({
                type: "stop_chat_stream",
                task_id: current_task_id,
                user: "unused"
            }));
            botui_stopAIButton.disabled = true;
            setTimeout(() => {
                botui_addMessage('<i>正在尝试停止AI输出...</i>', 'bot');
            }, 0);
        } else {
            console.warn("Cannot stop AI: No active task_id or WebSocket not open.");
            if (!current_task_id) {
                botui_stopAIButton.disabled = true;
            }
        }
    });
    botui_chatInput.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            botui_handleFormSubmission();
        }
    });
    botui_chatInput.addEventListener('input', () => {
        botui_chatInput.style.height = 'auto';
        requestAnimationFrame(() => {
             botui_chatInput.style.height = (botui_chatInput.scrollHeight) + 'px';
        });
    });

    console.log("Chat DOM elements and event listeners initialized.");
}

function botui_handleFormSubmission() {
    const query = botui_chatInput.value.trim();
    if (query) {
        botui_addMessage(query, 'user');
        current_task_id = null; // Reset task_id for new query
        botui_stopAIButton.disabled = true; // Disable button
        botui_sendButton.disabled = true; // 禁用发送按钮
        botui_streamMessageViaWebSocket(query);
        botui_chatInput.value = '';
        botui_chatInput.style.height = 'auto';
    }
}

// Event listeners are now attached in initializeChatDOMElements

function botui_addMessage(content, type, referenceNode = null) {
    const messageElement = document.createElement('div');
    messageElement.className = `message ${type}-message`;
    if (type === 'user') {
        messageElement.textContent = content; // Use textContent for user messages to prevent XSS
    } else {
        messageElement.innerHTML = content;
    }
    if (referenceNode) {
        botui_messageWindow.insertBefore(messageElement, referenceNode);
    } else {
        botui_messageWindow.appendChild(messageElement);
    }
    botui_scrollToBottom();
    return messageElement;
}

function botui_addCollapsibleMessage(summaryText, contentText, referenceNode = null) {
    if (!contentText) return;
    const detailsElement = document.createElement('details');
    detailsElement.className = 'message agent-thought';
    const summaryElement = document.createElement('summary');
    summaryElement.textContent = summaryText;
    const contentDiv = document.createElement('div');
    contentDiv.className = 'collapsible-content';
    contentDiv.innerHTML = marked.parse(contentText);
    detailsElement.appendChild(summaryElement);
    detailsElement.appendChild(contentDiv);

    // 确保在 finalize 之后，新的 collapsible message 被正确添加
    // 如果 referenceNode 存在且仍在 DOM 中，则在其前插入
    if (referenceNode && referenceNode.parentNode === botui_messageWindow) {
        botui_messageWindow.insertBefore(detailsElement, referenceNode);
    } else {
        // 否则，直接追加到末尾
        botui_messageWindow.appendChild(detailsElement);
    }
    setTimeout(botui_scrollToBottom, 0);
}

function botui_scrollToBottom() {
    botui_messageWindow.scrollTop = botui_messageWindow.scrollHeight;
}

function botui_appendToCurrentBotMessage(textChunk) {
    if (!isStreaming) return; // 如果已停止，则不处理
    if (!currentBotMessageElement) {
        const thinkingHTML = `<div class="thinking"><span></span><span></span><span></span></div>`;
        currentBotMessageElement = botui_addMessage(thinkingHTML, 'bot');
        currentBotMessageElement.dataset.fullText = ""; // 初始化完整文本
    }

    const thinkingIndicator = currentBotMessageElement.querySelector('.thinking');
    if (thinkingIndicator) { // 首次接收到文本块时，移除loading动画
        currentBotMessageElement.innerHTML = ''; // 清空loading
    }

    currentBotMessageElement.dataset.fullText += textChunk;
    currentBotMessageElement.innerHTML = marked.parse(currentBotMessageElement.dataset.fullText);
    botui_scrollToBottom();
}

function botui_getCurrentBotMessageElement() {
    return currentBotMessageElement;
}

function botui_finalizeCurrentBotMessage() {
    if (currentBotMessageElement && currentBotMessageElement.classList.contains('bot-message')) {
        const thinkingIndicator = currentBotMessageElement.querySelector('.thinking');
        // 如果消息元素仍然只包含 thinking 动画（即没有收到任何文本），
        // 可以选择移除它或替换为一个“无回复”的提示。
        if (thinkingIndicator && (!currentBotMessageElement.dataset.fullText || currentBotMessageElement.dataset.fullText.trim() === '')) {
             currentBotMessageElement.innerHTML = marked.parse("..."); // 或其他占位符，或移除
        }
    }
    currentBotMessageElement = null; // 重置，以便下次创建新消息
    botui_scrollToBottom();
}

async function botui_streamMessageViaWebSocket(query) {
    if (!chatSocket || chatSocket.readyState !== WebSocket.OPEN) {
        console.warn("聊天WebSocket未连接或未打开。");
        botui_addMessage("与聊天服务器的连接已断开。请稍后重试或刷新页面。", "bot");
        if (botui_sendButton) botui_sendButton.disabled = false;
        // The main page's `handleChatWebSocketDisconnect` will handle reconnection attempts.
        return;
    }

    isStreaming = true; // 开始流式传输
    current_task_id = null; // Reset task_id for the new stream
    botui_stopAIButton.disabled = true; // Ensure stop button is disabled at the start of a new stream


    // Initialize a new bot message element for thinking animation and subsequent stream
    const thinkingHTML = `<div class="thinking"><span></span><span></span><span></span></div>`;
    currentBotMessageElement = botui_addMessage(thinkingHTML, 'bot'); // This is now a global var
    currentBotMessageElement.dataset.fullText = ""; // Initialize

    const payload = {
        query: query,
        user: "unused", // 可以根据实际用户身份调整
        conversation_id: current_conversation_id, // 使用新的 conversation_id
        inputs: {} // 其他可能的输入参数
    };

    try {
        console.log("通过WebSocket发送聊天请求:", payload);
        chatSocket.send(JSON.stringify(payload));
    } catch (error) {
        console.error("通过WebSocket发送消息失败:", error);
        if (currentBotMessageElement) {
             currentBotMessageElement.innerHTML = marked.parse(`😥 **抱歉，发送消息时出错了**: \n\n\`\`\`\n${error.message}\n\`\`\``);
        } else {
            botui_addMessage(`😥 **抱歉，发送消息时出错了**: \n\n\`\`\`\n${error.message}\n\`\`\``, 'bot');
        }
        botui_finalizeCurrentBotMessage(); // 确保重置
        botui_sendButton.disabled = false; // 发送失败时重新启用发送按钮
    }
    // 注意：WebSocket是异步的，消息的接收和处理在 chatSocket.onmessage 中进行
}
