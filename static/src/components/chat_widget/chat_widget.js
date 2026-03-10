/** @odoo-module **/

import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class ChatWidget extends Component {
    static template = "inventory_ai.ChatWidget";

    setup() {
        this.action = useService("action");
        this.orm = useService("orm");
    }

    async openAIChat() {
        try {
            const channels = await this.orm.searchRead(
                "discuss.channel",
                [["name", "=", "AI Inventory Assistant"]],
                ["id"]
            );

            if (channels && channels.length > 0) {
                const channelId = channels[0].id;
                // Open Discuss module with the specific channel active
                this.action.doAction("mail.action_discuss", {
                    additionalContext: {
                        active_id: channelId,
                    },
                    clear_breadcrumbs: true,
                });
            } else {
                console.warn("AI Inventory Assistant channel not found. Opening general Discuss.");
                this.action.doAction("mail.action_discuss");
            }
        } catch (error) {
            console.error("Error opening AI Chat:", error);
            this.action.doAction("mail.action_discuss");
        }
    }
}

registry.category("systray").add("InventoryAIChatWidget", {
    Component: ChatWidget,
}, { sequence: 10 });
