"""
agent.py — The agent loop.

This is where everything connects:
- The system prompt (from prompts.py)
- The tools (from tools.py)
- The model (Qwen 2.5 via Ollama)

One main function: analyze_customer(customer_id). It runs the agent
loop until the model produces a final answer (or hits the iteration limit),
then returns the recommendation that was recorded.
"""

"""
agent.py — Local Ollama version of the AR agent.

This version runs on Qwen 2.5 7B locally via Ollama — free, private,
no API key needed. Useful for development and demos where privacy matters.

To use this version:
    1. Install Ollama: https://ollama.com
    2. Pull the model: ollama pull qwen2.5:7b
    3. Start Ollama: ollama serve
    4. Run: python -m src.agent

Note: The Claude version (agent_claude.py) produces higher-quality
output for demos. This version demonstrates the architecture is
model-agnostic — swap one config line to switch providers.
"""

import json
import ollama

from src.prompts import SYSTEM_PROMPT
from src import tools


# ---------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------

MODEL_NAME = "qwen2.5:7b"  # Run: ollama pull qwen2.5:7b
MAX_ITERATIONS = 8  # Safety cap — agent can call tools up to 8 times per customer


# ---------------------------------------------------------------------
# Tool schemas — what we send to the model so it knows what tools exist
# and what arguments each one takes.
#
# These follow the OpenAI function-calling format, which Ollama supports.
# The descriptions are critical: the model reads them to decide when to
# call each tool.
# ---------------------------------------------------------------------

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_customer_profile",
            "description": "Returns basic profile information for a customer: company name (or null for individuals), contact name (may be null), contact email, payment terms in days, credit limit, account age in months, and customer_type (business or individual).",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "string",
                        "description": "The customer identifier, e.g. 'C001'"
                    }
                },
                "required": ["customer_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_open_invoices",
            "description": "Returns a list of all currently open or overdue invoices for a customer. Each invoice includes invoice_id, issue_date, due_date, amount, status, days_outstanding, and days_past_due. Returns empty list if no open invoices.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "string",
                        "description": "The customer identifier, e.g. 'C001'"
                    }
                },
                "required": ["customer_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_payment_stats",
            "description": "Returns pre-computed payment behavior statistics: total_invoices_paid, avg_days_to_pay, median_days_to_pay, std_dev_days_to_pay, recent_avg_days_to_pay (last 3 invoices), drift_days (recent vs lifetime average), trend (improving/stable/deteriorating), reliability_score (0-1, fraction paid on time), behavior_classification (reliable/deteriorating_reliable/slow_but_consistent/erratic/problematic/mixed/insufficient_data), recent_paid_invoices (last 60 days), and if open invoices exist: current_days_outstanding and current_deviation_sigmas.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "string",
                        "description": "The customer identifier, e.g. 'C001'"
                    }
                },
                "required": ["customer_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_communications_log",
            "description": "Returns all prior reminder emails sent to this customer about their currently open invoices, plus comms about invoices paid in the last 60 days. Each entry shows tier (1, 2, or 3), date_sent, whether the customer responded, and any response summary. Returns empty list if no prior comms exist.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "string",
                        "description": "The customer identifier, e.g. 'C001'"
                    }
                },
                "required": ["customer_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "record_recommendation",
            "description": "Save the final recommendation for this customer. Call this ONCE at the end after gathering all information. After calling this, do not call any more tools.",
            "parameters": {
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
    }
]


# ---------------------------------------------------------------------
# Tool dispatcher — maps tool name strings to the actual Python functions.
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
    """
    Run a tool by name with the given arguments.
    Returns the tool's return value as a JSON-serializable object.
    Guards against unknown tool names (open models occasionally hallucinate these).
    """
    if tool_name not in TOOL_FUNCTIONS:
        return {"error": f"Tool '{tool_name}' does not exist. Available tools: {list(TOOL_FUNCTIONS.keys())}"}
    
    try:
        result = TOOL_FUNCTIONS[tool_name](**arguments)
        return result
    except TypeError as e:
        return {"error": f"Invalid arguments for {tool_name}: {str(e)}"}
    except Exception as e:
        return {"error": f"Tool {tool_name} failed: {str(e)}"}


def _log(message):
    """Print a timestamped log line. Replace with proper logging later."""
    print(f"  [agent] {message}")


# ---------------------------------------------------------------------
# The main entry point
# ---------------------------------------------------------------------

def analyze_customer(customer_id, verbose=True):
    """
    Run the agent loop for one customer.
    Returns the recommendation that was recorded, or an escalation
    recommendation if the loop hit its iteration limit.
    """
    if verbose:
        _log(f"Starting analysis for {customer_id}")
    
    # Track recommendations before this call so we can find the new one
    pre_call_recommendations = len(tools.get_all_recommendations())
    
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Analyze customer {customer_id} and recommend an action."}
    ]
    
    for iteration in range(MAX_ITERATIONS):
        if verbose:
            _log(f"Iteration {iteration + 1}: calling model")
        
        # Send the current conversation + available tools to the model
        response = ollama.chat(
            model=MODEL_NAME,
            messages=messages,
            tools=TOOL_SCHEMAS,
        )
        
        assistant_message = response["message"]
        messages.append(assistant_message)
        
        # Check if the model wants to call tools
        tool_calls = assistant_message.get("tool_calls", [])
        
        if not tool_calls:
            # No tool calls = the model has produced its final text answer
            if verbose:
                _log("No tool calls — model finished")
            break
        
        # Execute each tool call and append the result.
        # If the agent calls record_recommendation, treat it as the end of the loop:
        # execute it once, then exit. Any further record_recommendation calls in the
        # same batch are rejected to prevent the model from clobbering its own output.
        recommendation_recorded = False
        
        for tool_call in tool_calls:
            function_info = tool_call["function"]
            tool_name = function_info["name"]
            arguments = function_info.get("arguments", {})
            
            # Arguments may arrive as a string or dict depending on the model
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    arguments = {}
            
            if verbose:
                _log(f"Tool call: {tool_name}({arguments})")
            
            # If we've already recorded this turn, reject duplicate record_recommendation
            # calls and tell the model to stop
            if tool_name == "record_recommendation" and recommendation_recorded:
                if verbose:
                    _log("Rejecting duplicate record_recommendation call")
                messages.append({
                    "role": "tool",
                    "content": json.dumps({
                        "error": "Recommendation already recorded for this customer. Do not call record_recommendation again. Respond with a brief confirmation that you are done."
                    }),
                    "name": tool_name,
                })
                continue
            
            result = _execute_tool(tool_name, arguments)
            
            messages.append({
                "role": "tool",
                "content": json.dumps(result, default=str),
                "name": tool_name,
            })
            
            # Mark that we've now recorded — any further calls this turn get rejected
            if tool_name == "record_recommendation" and "error" not in result:
                recommendation_recorded = True
        
        # If the agent recorded a recommendation this iteration, exit the loop.
        # No need to call the model again — we have what we need.
        if recommendation_recorded:
            if verbose:
                _log("Recommendation recorded — exiting loop")
            break
    else:
        # for/else: this runs only if the loop completed without `break`
        # i.e., we hit MAX_ITERATIONS without a final answer
        if verbose:
            _log(f"Hit max iterations ({MAX_ITERATIONS}) without final answer — forcing escalation")
        tools.record_recommendation(
            customer_id=customer_id,
            classification="red",
            pattern_noticed="Agent did not complete analysis within iteration limit",
            recommended_action="escalate_to_human",
            drafted_email=None,
            reasoning="The agent loop exceeded its safety cap without producing a final recommendation. This indicates either a tool error, model confusion, or an edge case worth human review."
        )
    
    # Find the recommendation that was recorded during this call
    all_recommendations = tools.get_all_recommendations()
    if len(all_recommendations) > pre_call_recommendations:
        new_recommendation = all_recommendations[-1]
        if verbose:
            _log(f"Recommendation: {new_recommendation['classification']} — {new_recommendation['recommended_action']}")
        return new_recommendation
    else:
        # Model never called record_recommendation — defensive fallback
        if verbose:
            _log("Model never called record_recommendation — forcing escalation")
        fallback = {
            "customer_id": customer_id,
            "classification": "red",
            "pattern_noticed": "Agent did not record a recommendation",
            "recommended_action": "escalate_to_human",
            "drafted_email": None,
            "reasoning": "The agent produced text output but did not call record_recommendation. Likely a prompt-following failure."
        }
        tools._RECOMMENDATIONS.append(fallback)
        return fallback


# ---------------------------------------------------------------------
# Self-test — run this file directly to test the agent on one customer
# ---------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    
    # Allow specifying a customer ID from the command line, default to C001
    customer_id = sys.argv[1] if len(sys.argv) > 1 else "C001"
    
    print(f"\n{'='*60}")
    print(f"AGENT TEST — analyzing customer {customer_id}")
    print(f"{'='*60}\n")
    
    tools.reset_recommendations()
    recommendation = analyze_customer(customer_id, verbose=True)
    
    print(f"\n{'='*60}")
    print("FINAL RECOMMENDATION")
    print(f"{'='*60}")
    print(json.dumps(recommendation, indent=2, default=str))