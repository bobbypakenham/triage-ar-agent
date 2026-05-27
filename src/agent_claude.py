"""
agent_claude.py — The agent loop, powered by Claude.

Same architecture as agent.py but uses Anthropic's Claude API instead
of local Ollama. Identical tools, identical system prompt, identical
orchestrator interface. The only thing that changes is the model.

Why we have two agent files:
- agent.py runs on local Qwen via Ollama (free, slower, less accurate)
- agent_claude.py runs on Claude (paid, faster, more accurate)
Both expose the same analyze_customer() function.
"""

import json
import os
import time
from dotenv import load_dotenv
import anthropic

from src.prompts import SYSTEM_PROMPT
from src import tools


# ---------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------

load_dotenv()  # Read .env so ANTHROPIC_API_KEY is available
import streamlit as st
api_key = st.secrets.get("ANTHROPIC_API_KEY") if hasattr(st, "secrets") else None
api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
MODEL_NAME = "claude-sonnet-4-6"
MAX_ITERATIONS = 8
MAX_TOKENS_PER_RESPONSE = 2048  # Claude needs a max_tokens cap per call


# ---------------------------------------------------------------------
# Tool schemas — Anthropic uses input_schema instead of nested parameters.
# Otherwise the shape is the same as OpenAI/Ollama format.
# ---------------------------------------------------------------------

TOOL_SCHEMAS = [
    {
        "name": "get_customer_profile",
        "description": "Returns basic profile information for a customer: company name (or null for individuals), contact name (may be null), contact email, payment terms in days, credit limit, account age in months, and customer_type (business or individual).",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {
                    "type": "string",
                    "description": "The customer identifier, e.g. 'C001'"
                }
            },
            "required": ["customer_id"]
        }
    },
    {
        "name": "get_open_invoices",
        "description": "Returns a list of all currently open or overdue invoices for a customer. Each invoice includes invoice_id, issue_date, due_date, amount, status, days_outstanding, and days_past_due. Returns empty list if no open invoices.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {
                    "type": "string",
                    "description": "The customer identifier, e.g. 'C001'"
                }
            },
            "required": ["customer_id"]
        }
    },
    {
        "name": "get_payment_stats",
        "description": "Returns pre-computed payment behavior statistics: total_invoices_paid, avg_days_to_pay, median_days_to_pay, std_dev_days_to_pay, recent_avg_days_to_pay (last 3 invoices), drift_days (recent vs lifetime average), trend (improving/stable/deteriorating), reliability_score (0-1, fraction paid on time), avg_days_late (avg days past due, negative=early), behavior_classification (reliable/deteriorating_reliable/slightly_late/moderately_late/high_risk/erratic/slow_but_consistent/mixed/insufficient_data), recent_paid_invoices (last 60 days), and if open invoices exist: current_days_outstanding and current_deviation_sigmas (only present when meaningful).",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {
                    "type": "string",
                    "description": "The customer identifier, e.g. 'C001'"
                }
            },
            "required": ["customer_id"]
        }
    },
    {
        "name": "get_communications_log",
        "description": "Returns all prior reminder emails sent to this customer about their currently open invoices, plus comms about invoices paid in the last 60 days. Each entry shows tier (1, 2, or 3), date_sent, whether the customer responded, and any response summary. Returns empty list if no prior comms exist.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {
                    "type": "string",
                    "description": "The customer identifier, e.g. 'C001'"
                }
            },
            "required": ["customer_id"]
        }
    },
    {
        "name": "record_recommendation",
        "description": "Save the final recommendation for this customer. Call this ONCE at the end after gathering all information. After calling this, do not call any more tools.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "classification": {
                    "type": "string",
                    "enum": ["green", "amber", "red"]
                },
                "pattern_noticed": {
                    "type": "string",
                    "description": "One sentence describing what you observed about this customer."
                },
                "recommended_action": {
                    "type": "string",
                    "enum": ["no_action", "send_tier_1", "send_tier_2", "send_tier_3", "escalate_to_human"]
                },
                "drafted_email": {
                    "type": ["object", "null"],
                    "description": "Object with 'subject' and 'body' strings, or null if no email is being sent.",
                    "properties": {
                        "subject": {"type": "string"},
                        "body": {"type": "string"}
                    }
                },
                "reasoning": {
                    "type": "string",
                    "description": "2-3 sentences explaining why this action was chosen, referencing specific stats or facts."
                }
            },
            "required": ["customer_id", "classification", "pattern_noticed", "recommended_action", "reasoning"]
        }
    }
]


# ---------------------------------------------------------------------
# Tool dispatcher — same as Ollama version, maps tool names to functions
# ---------------------------------------------------------------------

TOOL_FUNCTIONS = {
    "get_customer_profile": tools.get_customer_profile,
    "get_open_invoices": tools.get_open_invoices,
    "get_payment_stats": tools.get_payment_stats,
    "get_communications_log": tools.get_communications_log,
    "record_recommendation": tools.record_recommendation,
}


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _execute_tool(tool_name, arguments):
    """Run a tool by name with the given arguments. Returns dict."""
    if tool_name not in TOOL_FUNCTIONS:
        return {"error": f"Tool '{tool_name}' does not exist. Available tools: {list(TOOL_FUNCTIONS.keys())}"}
    try:
        return TOOL_FUNCTIONS[tool_name](**arguments)
    except TypeError as e:
        return {"error": f"Invalid arguments for {tool_name}: {str(e)}"}
    except Exception as e:
        return {"error": f"Tool {tool_name} failed: {str(e)}"}


def _log(message):
    """Print a timestamped log line."""
    print(f"  [agent] {message}")


def _call_with_retry(client, messages, max_retries=3):
    """Call the Claude API with exponential backoff on rate limit errors."""
    for attempt in range(max_retries):
        try:
            return client.messages.create(
                model=MODEL_NAME,
                max_tokens=MAX_TOKENS_PER_RESPONSE,
                system=SYSTEM_PROMPT,
                tools=TOOL_SCHEMAS,
                messages=messages,
            )
        except anthropic.RateLimitError as e:
            if attempt == max_retries - 1:
                raise
            wait = 30 * (2 ** attempt)  # 30s, 60s, 120s
            _log(f"Rate limit hit — waiting {wait}s before retry {attempt + 2}/{max_retries}")
            time.sleep(wait)
        except anthropic.APIStatusError as e:
            if e.status_code in (500, 529) and attempt < max_retries - 1:
                wait = 10 * (2 ** attempt)
                _log(f"API error {e.status_code} — waiting {wait}s before retry")
                time.sleep(wait)
            else:
                raise


# ---------------------------------------------------------------------
# The main entry point
# ---------------------------------------------------------------------

def analyze_customer(customer_id, verbose=True):
    """
    Run the agent loop for one customer using Claude.
    Returns the recommendation that was recorded, or an escalation
    recommendation if the loop hit its iteration limit.
    """
    if verbose:
        _log(f"Starting analysis for {customer_id}")
    
    pre_call_recommendations = len(tools.get_all_recommendations())
    
    client = anthropic.Anthropic(api_key=api_key)
    
    # Claude's API takes the system prompt as a separate parameter,
    # not as a message with role='system'.
    messages = [
        {"role": "user", "content": f"Analyze customer {customer_id} and recommend an action."}
    ]
    
    for iteration in range(MAX_ITERATIONS):
        if verbose:
            _log(f"Iteration {iteration + 1}: calling Claude")
        
        response = _call_with_retry(client, messages)
        
        # Claude returns content as a list of "blocks". Each block is either
        # text or a tool_use. We append the whole assistant message as-is.
        messages.append({"role": "assistant", "content": response.content})
        
        # Find all tool_use blocks in this response
        tool_use_blocks = [block for block in response.content if block.type == "tool_use"]
        
        # If there are no tool calls, the model is done
        if not tool_use_blocks:
            if verbose:
                _log("No tool calls — model finished")
            break
        
        # Execute tool calls and prepare results
        # Special handling: if record_recommendation is called, that's end-of-loop
        recommendation_recorded = False
        tool_results = []
        
        for block in tool_use_blocks:
            tool_name = block.name
            arguments = block.input
            tool_use_id = block.id
            
            if verbose:
                _log(f"Tool call: {tool_name}({json.dumps(arguments, default=str)[:200]})")
            
            # Reject duplicate record_recommendation calls
            if tool_name == "record_recommendation" and recommendation_recorded:
                if verbose:
                    _log("Rejecting duplicate record_recommendation call")
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": json.dumps({
                        "error": "Recommendation already recorded. Do not call again."
                    }),
                })
                continue
            
            result = _execute_tool(tool_name, arguments)
            
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": json.dumps(result, default=str),
            })
            
            if tool_name == "record_recommendation" and "error" not in result:
                recommendation_recorded = True
        
        # Append tool results as a user message (Claude's convention)
        messages.append({"role": "user", "content": tool_results})
        
        # Exit the loop if we recorded a recommendation this iteration
        if recommendation_recorded:
            if verbose:
                _log("Recommendation recorded — exiting loop")
            break
    else:
        # Hit max iterations without breaking — force escalation
        if verbose:
            _log(f"Hit max iterations ({MAX_ITERATIONS}) — forcing escalation")
        tools.record_recommendation(
            customer_id=customer_id,
            classification="red",
            pattern_noticed="Agent did not complete analysis within iteration limit",
            recommended_action="escalate_to_human",
            drafted_email=None,
            reasoning="The agent loop exceeded its safety cap without producing a final recommendation."
        )
    
    # Return the recommendation that was just recorded
    all_recommendations = tools.get_all_recommendations()
    if len(all_recommendations) > pre_call_recommendations:
        new_recommendation = all_recommendations[-1]
        if verbose:
            _log(f"Recommendation: {new_recommendation['classification']} — {new_recommendation['recommended_action']}")
        return new_recommendation
    else:
        if verbose:
            _log("Model never called record_recommendation — forcing escalation")
        fallback = {
            "customer_id": customer_id,
            "classification": "red",
            "pattern_noticed": "Agent did not record a recommendation",
            "recommended_action": "escalate_to_human",
            "drafted_email": None,
            "reasoning": "The agent produced text output but did not call record_recommendation."
        }
        tools._RECOMMENDATIONS.append(fallback)
        return fallback


# ---------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    
    customer_id = sys.argv[1] if len(sys.argv) > 1 else "C001"
    
    print(f"\n{'='*60}")
    print(f"CLAUDE AGENT TEST — analyzing customer {customer_id}")
    print(f"{'='*60}\n")
    
    tools.reset_recommendations()
    recommendation = analyze_customer(customer_id, verbose=True)
    
    print(f"\n{'='*60}")
    print("FINAL RECOMMENDATION")
    print(f"{'='*60}")
    print(json.dumps(recommendation, indent=2, default=str))