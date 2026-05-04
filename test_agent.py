import sys
import io

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from engine import ClawdEngine

def test():
    print("Initializing Clawd Engine...")
    engine = ClawdEngine()
    
    print("Testing Connection:", engine.test_connection())
    
    prompts = [
        "Scan 127.0.0.1 with nmap. Try to find open ports.",
        "Based on your scan, what facts did you record about 127.0.0.1?"
    ]
    
    for i, p in enumerate(prompts):
        print("\n\n" + "="*50)
        print(f"[{i+1}] USER: {p}")
        print("-" * 50)
        
        resp = engine.chat(p)
        
        print("\nCLAWD FINAL RESPONSE:")
        print(resp)
        print("="*50)

if __name__ == "__main__":
    test()
