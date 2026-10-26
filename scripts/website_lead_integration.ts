/**
 * Samvaad AI / Psyber Voice Agent Integration Helper
 * 
 * Embed this in Wedeazzy, Hotelzz, or Pets24x7 to submit lead form data directly
 * into the AI Voice Agent pipeline and trigger automated qualification calling.
 */

export const VOICE_AGENT_CONFIG = {
  // Replace with your production domain or ngrok public base URL
  BASE_URL: "https://your-domain.com", 
  ENDPOINTS: {
    wedeazzy: {
      agentId: 2,
      token: "GZDPcQ6Gl8Ry-W7unE29mcXfIR9R7SOa",
    },
    hotelzz: {
      agentId: 3,
      token: "CfZcOgx6HqP_c9IwgscblFpXSY7OtEyj",
    },
    pets24x7: {
      agentId: 4,
      token: "AKZAB1wH1HrwGHwEXT6PAIjsnuu8kE_5",
    },
  },
};

export async function submitLeadToVoiceAgent(
  website: "wedeazzy" | "hotelzz" | "pets24x7",
  leadData: {
    name: string;
    phone: string;
    email?: string;
    city?: string;
    message?: string;
    [key: string]: any;
  },
  customBaseUrl?: string
) {
  const cfg = VOICE_AGENT_CONFIG.ENDPOINTS[website];
  if (!cfg) throw new Error(`Unknown website target: ${website}`);

  const baseUrl = customBaseUrl || VOICE_AGENT_CONFIG.BASE_URL;
  const endpoint = `${baseUrl}/api/public/agents/${cfg.agentId}/leads?token=${cfg.token}`;

  const response = await fetch(endpoint, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(leadData),
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`Lead submission failed (${response.status}): ${errorText}`);
  }

  return await response.json();
}
