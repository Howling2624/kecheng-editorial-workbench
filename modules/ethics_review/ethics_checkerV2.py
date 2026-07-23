import os
import re
import json
from pathlib import Path
from typing import List, Dict, Tuple
from datetime import datetime
import requests

# 需要安装的库:
# pip install pypdf python-docx requests

try:
    import pypdf
    from docx import Document
except ImportError:
    print("请先安装依赖: pip install pypdf python-docx requests")
    exit(1)


class EthicsContentDetector:
    """学术稿件伦理审批内容检测器 - DeepSeek版本"""
    
    def __init__(self, api_key: str = None, api_url: str = None, model: str = None):
        """
        初始化检测器
        api_key: DeepSeek API密钥，如果不提供则从环境变量读取
        """
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY")
        if not self.api_key:
            print("警告: 未设置API密钥，AI分析功能将不可用")
        
        self.api_url = api_url or os.environ.get(
            "DEEPSEEK_API_URL",
            "https://api.deepseek.com/v1/chat/completions",
        )
        self.model = model or os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
        
        # 关键词字典（用于初筛）
        self.keywords = {
            'high_priority': [
                'ethics approval', 'ethical approval', 'ethics committee',
                'institutional review board', 'IRB', 'informed consent',
                'questionnaire', 'survey', 'interview', 'participant',
                'fMRI', 'EEG', 'neuroimaging', 'clinical trial',
                'intervention', 'randomized', 'patient', 'subject',
                '伦理', '伦理委员会', '知情同意', '问卷', '访谈',
                '受试者', '临床试验', '干预', '神经影像'
            ],
            'method_sections': [
                'method', 'methodology', 'procedure', 'participant',
                'material', 'experiment', 'design', 'protocol',
                '方法', '实验', '被试', '程序'
            ]
        }
    
    def extract_text_from_pdf(self, pdf_path: str) -> str:
        """从PDF提取文本"""
        try:
            reader = pypdf.PdfReader(pdf_path)
            text = ""
            for page in reader.pages:
                text += page.extract_text() + "\n"
            return text
        except Exception as e:
            print(f"PDF读取错误 {pdf_path}: {e}")
            return ""
    
    def extract_text_from_docx(self, docx_path: str) -> str:
        """从Word文档提取文本"""
        try:
            doc = Document(docx_path)
            text = "\n".join([para.text for para in doc.paragraphs])
            return text
        except Exception as e:
            print(f"Word读取错误 {docx_path}: {e}")
            return ""
    
    def extract_text(self, file_path: str) -> str:
        """根据文件类型提取文本"""
        file_path = Path(file_path)
        if file_path.suffix.lower() == '.pdf':
            return self.extract_text_from_pdf(str(file_path))
        elif file_path.suffix.lower() in ['.docx', '.doc']:
            return self.extract_text_from_docx(str(file_path))
        else:
            raise ValueError(f"不支持的文件格式: {file_path.suffix}")
    
    def quick_keyword_check(self, text: str) -> Tuple[bool, List[str]]:
        """
        快速关键词检查
        返回: (是否可能包含伦理内容, 命中的关键词列表)
        """
        text_lower = text.lower()
        hits = []
        
        for keyword in self.keywords['high_priority']:
            if keyword.lower() in text_lower:
                hits.append(keyword)
        
        # 如果没有任何关键词命中，很可能不涉及伦理审批
        if len(hits) == 0:
            return False, []
        
        return True, hits
    
    def extract_relevant_sections(self, text: str, max_chars: int = 8000) -> str:
        """
        提取最相关的文本段落
        优先提取Methods部分和包含关键词的段落
        """
        paragraphs = text.split('\n')
        paragraphs = [p.strip() for p in paragraphs if len(p.strip()) > 50]
        
        # 评分系统：为每个段落打分
        scored_paragraphs = []
        
        for para in paragraphs:
            score = 0
            para_lower = para.lower()
            
            # Methods相关章节标题得高分
            for method_keyword in self.keywords['method_sections']:
                if method_keyword in para_lower[:100]:  # 只检查段落开头
                    score += 10
            
            # 包含高优先级关键词
            for keyword in self.keywords['high_priority']:
                if keyword.lower() in para_lower:
                    score += 5
            
            # 包含ethics/approval等明确词汇
            if 'ethic' in para_lower or 'approval' in para_lower or '伦理' in para:
                score += 15
            
            if score > 0:
                scored_paragraphs.append((score, para))
        
        # 按分数排序，取top段落
        scored_paragraphs.sort(reverse=True, key=lambda x: x[0])
        
        selected_text = ""
        for score, para in scored_paragraphs:
            if len(selected_text) + len(para) < max_chars:
                selected_text += para + "\n\n"
            else:
                break
        
        return selected_text if selected_text else text[:max_chars]
    
    def ai_analysis(self, text_segment: str, filename: str) -> Dict:
        """
        使用DeepSeek进行AI分析
        返回: {needs_ethics: bool, confidence: str, reason: str, categories: list}
        """
        if not self.api_key:
            return {
                "needs_ethics": None,
                "confidence": "unknown",
                "reason": "未配置API密钥",
                "categories": [],
                "key_evidence": []
            }
        
        prompt = f"""请分析以下学术稿件片段，判断研究是否需要伦理审批声明。

需要伦理审批的研究类型包括：
1. 匿名调查问卷、心理测评
2. fMRI/EEG等神经影像实验
3. 认知行为反应测试
4. 深度访谈
5. 教学干预效果评估
6. 新药临床试验
7. 新型医疗器械临床验证
8. 新外科手术方法疗效评估
9. 康复训练干预
10. 摄影或视频分析（如运动姿态研究）
11. 面部识别或人机交互研究
12. 其他涉及人类受试者的研究

请严格以JSON格式返回分析结果（不要添加任何markdown标记或其他文字）：
{{
  "needs_ethics": true,
  "confidence": "high",
  "reason": "判断理由，中文说明",
  "categories": ["涉及的具体研究类型"],
  "key_evidence": ["支持判断的关键证据句子，最多3条"]
}}

稿件片段：
{text_segment}
"""
        
        try:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}"
            }
            
            data = {
                "model": self.model,
                "messages": [
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.3,
                "max_tokens": 1000
            }
            
            response = requests.post(
                self.api_url,
                headers=headers,
                json=data,
                timeout=30
            )
            
            if response.status_code != 200:
                return {
                    "needs_ethics": None,
                    "confidence": "error",
                    "reason": f"API调用失败: {response.status_code}",
                    "categories": [],
                    "key_evidence": []
                }
            
            result_data = response.json()
            response_text = result_data['choices'][0]['message']['content']
            
            # 提取JSON部分（处理可能的markdown包裹）
            response_text = response_text.strip()
            if response_text.startswith('```'):
                response_text = re.sub(r'^```(?:json)?\s*\n', '', response_text)
                response_text = re.sub(r'\n```\s*$', '', response_text)
            
            # 尝试解析JSON
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
                return result
            else:
                return {
                    "needs_ethics": None,
                    "confidence": "low",
                    "reason": "AI返回格式异常",
                    "categories": [],
                    "key_evidence": []
                }
                
        except Exception as e:
            print(f"AI分析出错: {e}")
            return {
                "needs_ethics": None,
                "confidence": "error",
                "reason": f"分析失败: {str(e)}",
                "categories": [],
                "key_evidence": []
            }
    
    def detect_ethical_statement(self, text: str) -> Dict:
        """
        检测稿件中的Ethical Statement
        返回: {
            has_statement: bool,
            is_no_human_animal: bool,
            statement_text: str,
            confidence: str
        }
        """
        result = {
            "has_statement": False,
            "is_no_human_animal": False,
            "statement_text": "",
            "confidence": "none",
            "detection_method": ""
        }
        
        # 清理文本：移除多余空格和换行
        text_cleaned = re.sub(r'\s+', ' ', text)
        text_lower = text_cleaned.lower()
        
        # 1. 查找Ethical Statement章节标题
        ethical_headers = [
            r'ethic(?:al)?\s+statement',
            r'ethic(?:al)?\s+approval',
            r'ethic(?:al)?\s+consideration',
            r'statement\s+of\s+ethic',
            r'research\s+ethic',
            r'伦理声明',
            r'伦理说明'
        ]
        
        statement_found = False
        statement_position = -1
        matched_header = ""
        
        for pattern in ethical_headers:
            match = re.search(pattern, text_lower)
            if match:
                statement_found = True
                statement_position = match.start()
                matched_header = match.group()
                break
        
        if not statement_found:
            result["detection_method"] = "未找到Ethical Statement章节"
            return result
        
        result["has_statement"] = True
        
        # 2. 提取Ethical Statement后的内容（约500字符）
        extract_start = statement_position
        extract_end = min(statement_position + 1000, len(text_lower))
        statement_section = text_lower[extract_start:extract_end]
        
        # 同时提取原始文本（保留大小写）用于显示
        statement_section_original = text_cleaned[extract_start:extract_end]
        
        # 3. 检测"不涉及人类/动物"的多种表述方式
        no_human_animal_patterns = [
            # 标准表述
            r'(?:this|the)\s+study\s+(?:does\s+)?not\s+(?:contain|involve|include)\s+(?:any\s+)?(?:studies\s+with\s+)?(?:human|animal)',
            r'no\s+(?:human|animal)\s+(?:subjects|participants)',
            r'(?:does\s+)?not\s+(?:involve|include|require)\s+(?:human|animal)',
            
            # 变体
            r'without\s+(?:human|animal)\s+(?:subjects|participants)',
            r'did\s+not\s+involve\s+(?:human|animal)',
            r'not\s+performed\s+(?:on|with)\s+(?:human|animal)',
            
            # 中文
            r'不涉及.*?(?:人类|动物)',
            r'未涉及.*?(?:人类|动物)',
            r'没有.*?(?:人类|动物).*?受试者',
            
            # 宽松匹配（考虑格式问题）
            r'(?:study|research|work).*?not.*?(?:human|animal)',
            r'not.*?(?:studies|experiments).*?(?:human|animal)',
        ]
        
        matched_pattern = None
        confidence_score = 0
        
        for i, pattern in enumerate(no_human_animal_patterns):
            match = re.search(pattern, statement_section)
            if match:
                matched_pattern = match.group()
                # 前面的模式更精确，置信度更高
                if i < 3:
                    confidence_score = 3  # high
                elif i < 6:
                    confidence_score = 2  # medium
                else:
                    confidence_score = 1  # low
                break
        
        if matched_pattern:
            result["is_no_human_animal"] = True
            result["confidence"] = ["low", "low", "medium", "high"][confidence_score]
            result["detection_method"] = f"匹配到模式: {matched_pattern[:50]}..."
            
            # 尝试提取完整句子
            sentences = re.split(r'[.。!！?？\n]', statement_section_original)
            for sent in sentences:
                if len(sent.strip()) > 20 and any(kw in sent.lower() for kw in ['human', 'animal', '人类', '动物']):
                    result["statement_text"] = sent.strip()[:200]
                    break
            
            if not result["statement_text"]:
                result["statement_text"] = matched_pattern
        else:
            result["is_no_human_animal"] = False
            result["confidence"] = "uncertain"
            result["detection_method"] = "找到Ethical Statement章节，但未检测到标准免责声明"
            
            # 提取章节内容的前200字符作为参考
            first_sentence = statement_section_original[:200].strip()
            result["statement_text"] = first_sentence
        
        return result
    
    def process_file(self, file_path: str) -> Dict:
        """
        处理单个文件的完整流程
        """
        filename = Path(file_path).name
        print(f"\n处理文件: {filename}")
        
        result = {
            "filename": filename,
            "file_path": file_path,
            "stage1_keyword_check": False,
            "keywords_found": [],
            "stage2_ai_analysis": None,
            "final_decision": "unknown",
            "confidence": "unknown",
            "reason": "",
            "categories": [],
            "key_evidence": [],
            "process_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "ethical_statement": None
        }
        
        # Stage 1: 提取文本
        print("  [1/4] 提取文本...")
        text = self.extract_text(file_path)
        if not text:
            result["reason"] = "无法提取文本"
            return result
        
        # Stage 1.5: 检测Ethical Statement
        print("  [2/4] 检测Ethical Statement...")
        ethical_statement = self.detect_ethical_statement(text)
        result["ethical_statement"] = ethical_statement
        
        if ethical_statement["has_statement"]:
            if ethical_statement["is_no_human_animal"]:
                print(f"  ✓ 发现免责声明 (置信度: {ethical_statement['confidence']})")
            else:
                print(f"  ⚠ 发现Ethical Statement，但非标准免责声明")
        else:
            print("  ℹ 未发现Ethical Statement章节")
        
        # Stage 2: 关键词快速筛查
        print("  [3/4] 关键词筛查...")
        has_keywords, keywords = self.quick_keyword_check(text)
        result["stage1_keyword_check"] = has_keywords
        result["keywords_found"] = keywords
        
        if not has_keywords:
            result["final_decision"] = "likely_no"
            result["confidence"] = "medium"
            result["reason"] = "未发现相关关键词，可能不需要伦理审批"
            print("  ✓ 初步判断：可能不需要伦理审批")
            return result
        
        print(f"  ✓ 发现关键词: {', '.join(keywords[:5])}...")
        
        # Stage 3: AI精准分析
        print("  [4/4] AI深度分析...")
        relevant_text = self.extract_relevant_sections(text)
        ai_result = self.ai_analysis(relevant_text, filename)
        
        result["stage2_ai_analysis"] = ai_result
        needs_ethics = ai_result.get("needs_ethics")
        if needs_ethics is True:
            result["final_decision"] = "yes"
        elif needs_ethics is False:
            result["final_decision"] = "no"
        else:
            result["final_decision"] = "unknown"
        result["confidence"] = ai_result.get("confidence", "unknown")
        result["reason"] = ai_result.get("reason", "")
        result["categories"] = ai_result.get("categories", [])
        result["key_evidence"] = ai_result.get("key_evidence", [])
        
        if ai_result.get("needs_ethics"):
            print(f"  ⚠ 需要伦理审批 (置信度: {result['confidence']})")
        else:
            print(f"  ✓ 不需要伦理审批 (置信度: {result['confidence']})")
        
        return result
    
    def generate_single_html_report(self, result: Dict, output_dir: str):
        """为单个稿件生成HTML报告"""
        
        # 决策状态样式
        status_config = {
            "yes": {
                "color": "#ef4444",
                "bg": "#fee2e2",
                "icon": "⚠️",
                "text": "需要伦理审批"
            },
            "no": {
                "color": "#10b981",
                "bg": "#d1fae5",
                "icon": "✓",
                "text": "不需要伦理审批"
            },
            "likely_no": {
                "color": "#3b82f6",
                "bg": "#dbeafe",
                "icon": "ℹ️",
                "text": "可能不需要伦理审批"
            },
            "unknown": {
                "color": "#6b7280",
                "bg": "#f3f4f6",
                "icon": "?",
                "text": "未知状态"
            }
        }
        
        status = status_config.get(result["final_decision"], status_config["unknown"])
        confidence_map = {
            "high": "高",
            "medium": "中",
            "low": "低",
            "unknown": "未知",
            "error": "错误"
        }
        
        # 关键词标签HTML
        keywords_html = "".join([
            f'<span class="keyword-tag">{kw}</span>'
            for kw in result["keywords_found"][:10]
        ])
        
        # 研究类型标签HTML
        categories_html = "".join([
            f'<span class="category-tag">{cat}</span>'
            for cat in result["categories"]
        ]) if result["categories"] else '<span style="color: #9ca3af;">无</span>'
        
        # 关键证据HTML
        evidence_html = ""
        if result.get("key_evidence"):
            evidence_items = "".join([
                f'<li>{ev}</li>'
                for ev in result["key_evidence"]
            ])
            evidence_html = f'<ul class="evidence-list">{evidence_items}</ul>'
        else:
            evidence_html = '<p style="color: #9ca3af;">无关键证据</p>'
        
        # Ethical Statement HTML
        ethical_statement_html = ""
        if result.get("ethical_statement"):
            es = result["ethical_statement"]
            
            if es["has_statement"]:
                if es["is_no_human_animal"]:
                    es_status_color = "#10b981"
                    es_status_bg = "#d1fae5"
                    es_status_icon = "✓"
                    es_status_text = "有免责声明（不涉及人类/动物）"
                else:
                    es_status_color = "#f59e0b"
                    es_status_bg = "#fef3c7"
                    es_status_icon = "⚠️"
                    es_status_text = "有Ethical Statement（但非标准免责声明）"
            else:
                es_status_color = "#ef4444"
                es_status_bg = "#fee2e2"
                es_status_icon = "✗"
                es_status_text = "未发现Ethical Statement"
            
            es_confidence_badge = ""
            if es["confidence"] != "none":
                conf_class_map = {
                    "high": "confidence-high",
                    "medium": "confidence-medium",
                    "low": "confidence-low",
                    "uncertain": "confidence-low"
                }
                conf_text_map = {
                    "high": "高",
                    "medium": "中",
                    "low": "低",
                    "uncertain": "不确定"
                }
                es_confidence_badge = f'<span class="confidence-badge {conf_class_map.get(es["confidence"], "confidence-low")}">{conf_text_map.get(es["confidence"], "未知")}</span>'
            
            es_statement_text = es.get("statement_text", "")
            if es_statement_text:
                es_text_display = f'<div class="statement-text">{es_statement_text}</div>'
            else:
                es_text_display = '<p style="color: #9ca3af;">无法提取声明文本</p>'
            
            ethical_statement_html = f"""
            <div class="info-section">
                <div class="section-title">Ethical Statement 检测</div>
                <div class="ethical-statement-card" style="background: {es_status_bg}; border-left: 4px solid {es_status_color}; padding: 20px; border-radius: 8px;">
                    <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 12px;">
                        <span style="font-size: 24px;">{es_status_icon}</span>
                        <div style="flex: 1;">
                            <div style="font-size: 16px; font-weight: 600; color: {es_status_color};">{es_status_text}</div>
                            <div style="font-size: 13px; color: #6b7280; margin-top: 4px;">检测置信度: {es_confidence_badge if es_confidence_badge else "N/A"}</div>
                        </div>
                    </div>
                    <div style="background: white; padding: 16px; border-radius: 6px; margin-top: 12px;">
                        <div style="font-size: 13px; color: #6b7280; margin-bottom: 8px;">检测到的声明内容：</div>
                        {es_text_display}
                    </div>
                    <div style="font-size: 12px; color: #6b7280; margin-top: 12px; font-style: italic;">
                        检测方法: {es.get("detection_method", "未知")}
                    </div>
                </div>
            </div>
            """
        
        html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>伦理审批检测报告 - {result['filename']}</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 40px 20px;
        }}
        
        .container {{
            max-width: 900px;
            margin: 0 auto;
            background: white;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
            overflow: hidden;
        }}
        
        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 40px;
            text-align: center;
        }}
        
        .header h1 {{
            font-size: 28px;
            margin-bottom: 10px;
            font-weight: 600;
        }}
        
        .header .subtitle {{
            font-size: 14px;
            opacity: 0.9;
        }}
        
        .content {{
            padding: 40px;
        }}
        
        .status-card {{
            background: {status['bg']};
            border-left: 4px solid {status['color']};
            padding: 24px;
            border-radius: 12px;
            margin-bottom: 30px;
            display: flex;
            align-items: center;
            gap: 16px;
        }}
        
        .status-icon {{
            font-size: 40px;
        }}
        
        .status-content {{
            flex: 1;
        }}
        
        .status-title {{
            font-size: 24px;
            font-weight: 600;
            color: {status['color']};
            margin-bottom: 8px;
        }}
        
        .status-subtitle {{
            color: #6b7280;
            font-size: 14px;
        }}
        
        .info-section {{
            margin-bottom: 30px;
        }}
        
        .section-title {{
            font-size: 18px;
            font-weight: 600;
            color: #1f2937;
            margin-bottom: 16px;
            padding-bottom: 8px;
            border-bottom: 2px solid #e5e7eb;
        }}
        
        .info-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-bottom: 20px;
        }}
        
        .info-item {{
            background: #f9fafb;
            padding: 16px;
            border-radius: 8px;
        }}
        
        .info-label {{
            font-size: 12px;
            color: #6b7280;
            margin-bottom: 6px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        
        .info-value {{
            font-size: 16px;
            color: #1f2937;
            font-weight: 500;
        }}
        
        .keyword-tag {{
            display: inline-block;
            background: #ede9fe;
            color: #7c3aed;
            padding: 6px 12px;
            border-radius: 16px;
            font-size: 13px;
            margin: 4px;
        }}
        
        .category-tag {{
            display: inline-block;
            background: #fef3c7;
            color: #d97706;
            padding: 6px 12px;
            border-radius: 16px;
            font-size: 13px;
            margin: 4px;
            font-weight: 500;
        }}
        
        .reason-box {{
            background: #f0f9ff;
            border-left: 4px solid #3b82f6;
            padding: 20px;
            border-radius: 8px;
            color: #1e40af;
            line-height: 1.6;
        }}
        
        .evidence-list {{
            list-style: none;
            padding: 0;
        }}
        
        .evidence-list li {{
            background: #fef9e7;
            padding: 12px 16px;
            margin-bottom: 10px;
            border-radius: 8px;
            border-left: 3px solid #f59e0b;
            line-height: 1.6;
        }}
        
        .statement-text {{
            background: #f9fafb;
            padding: 12px;
            border-radius: 6px;
            border-left: 3px solid #6366f1;
            color: #1f2937;
            line-height: 1.6;
            font-size: 14px;
        }}
        
        .footer {{
            background: #f9fafb;
            padding: 24px 40px;
            text-align: center;
            color: #6b7280;
            font-size: 13px;
        }}
        
        .confidence-badge {{
            display: inline-block;
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 13px;
            font-weight: 600;
        }}
        
        .confidence-high {{
            background: #d1fae5;
            color: #065f46;
        }}
        
        .confidence-medium {{
            background: #fef3c7;
            color: #92400e;
        }}
        
        .confidence-low {{
            background: #fee2e2;
            color: #991b1b;
        }}
        
        @media (max-width: 768px) {{
            .container {{
                border-radius: 0;
            }}
            
            .header, .content, .footer {{
                padding: 24px;
            }}
            
            .info-grid {{
                grid-template-columns: 1fr;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📋 学术稿件伦理审批检测报告</h1>
            <div class="subtitle">Ethics Approval Detection Report</div>
        </div>
        
        <div class="content">
            <div class="status-card">
                <div class="status-icon">{status['icon']}</div>
                <div class="status-content">
                    <div class="status-title">{status['text']}</div>
                    <div class="status-subtitle">检测时间: {result['process_time']}</div>
                </div>
            </div>
            
            <div class="info-section">
                <div class="section-title">基本信息</div>
                <div class="info-grid">
                    <div class="info-item">
                        <div class="info-label">文件名称</div>
                        <div class="info-value">{result['filename']}</div>
                    </div>
                    <div class="info-item">
                        <div class="info-label">置信度</div>
                        <div class="info-value">
                            <span class="confidence-badge confidence-{result['confidence']}">
                                {confidence_map.get(result['confidence'], '未知')}
                            </span>
                        </div>
                    </div>
                    <div class="info-item">
                        <div class="info-label">关键词命中</div>
                        <div class="info-value">{len(result['keywords_found'])} 个</div>
                    </div>
                </div>
            </div>
            
            <div class="info-section">
                <div class="section-title">判断理由</div>
                <div class="reason-box">
                    {result['reason'] or '暂无详细说明'}
                </div>
            </div>
            
            {f'''<div class="info-section">
                <div class="section-title">涉及的研究类型</div>
                <div>
                    {categories_html}
                </div>
            </div>''' if result['categories'] else ''}
            
            {ethical_statement_html}
            
            {f'''<div class="info-section">
                <div class="section-title">关键证据</div>
                {evidence_html}
            </div>''' if result.get('key_evidence') else ''}
            
            <div class="info-section">
                <div class="section-title">检测到的关键词</div>
                <div>
                    {keywords_html if keywords_html else '<span style="color: #9ca3af;">未发现相关关键词</span>'}
                </div>
            </div>
        </div>
        
        <div class="footer">
            <p>本报告由学术稿件伦理审批检测系统自动生成</p>
            <p style="margin-top: 8px; opacity: 0.8;">
                Powered by DeepSeek AI | Generated on {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
            </p>
        </div>
    </div>
</body>
</html>"""
        
        # 保存HTML文件
        output_path = Path(output_dir) / f"{Path(result['filename']).stem}_report.html"
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        return str(output_path)
    
    def batch_process(self, folder_path: str, output_dir: str = "reports"):
        """
        批量处理文件夹中的所有稿件
        """
        folder = Path(folder_path)
        files = list(folder.glob("*.pdf")) + list(folder.glob("*.docx")) + list(folder.glob("*.doc"))
        
        # 创建输出目录
        output_path = Path(output_dir)
        output_path.mkdir(exist_ok=True)
        
        print(f"找到 {len(files)} 个文件待处理")
        print(f"报告将保存到: {output_path.absolute()}")
        
        results = []
        html_files = []
        
        for i, file_path in enumerate(files, 1):
            print(f"\n进度: {i}/{len(files)}")
            result = self.process_file(str(file_path))
            results.append(result)
            
            # 生成单独的HTML报告
            html_file = self.generate_single_html_report(result, output_dir)
            html_files.append(html_file)
            print(f"  ✓ HTML报告已生成: {Path(html_file).name}")
        
        # 保存JSON结果
        json_file = output_path / "all_results.json"
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        
        # 生成汇总HTML索引页
        self.generate_summary_html(results, html_files, output_dir)
        
        print(f"\n✅ 所有报告已生成完成！")
        print(f"📊 汇总报告: {output_path / 'index.html'}")
        print(f"📁 详细报告: {output_path}/ 目录下")
        
        return results
    
    def generate_summary_html(self, results: List[Dict], html_files: List[str], output_dir: str):
        """生成汇总HTML索引页"""
        
        needs_ethics = [r for r in results if r['final_decision'] == 'yes']
        no_ethics = [r for r in results if r['final_decision'] == 'no']
        likely_no = [r for r in results if r['final_decision'] == 'likely_no']
        unclear = [r for r in results if r['final_decision'] not in ['yes', 'no', 'likely_no']]
        
        # 生成表格行
        table_rows = ""
        for i, result in enumerate(results):
            status_class = {
                "yes": "status-danger",
                "no": "status-success",
                "likely_no": "status-info",
                "unknown": "status-gray"
            }.get(result['final_decision'], 'status-gray')
            
            status_text = {
                "yes": "需要审批",
                "no": "不需要",
                "likely_no": "可能不需要",
                "unknown": "未知"
            }.get(result['final_decision'], '未知')
            
            html_filename = Path(html_files[i]).name
            
            table_rows += f"""
            <tr>
                <td>{i+1}</td>
                <td class="filename-cell">
                    <a href="{html_filename}" target="_blank">{result['filename']}</a>
                </td>
                <td><span class="status-badge {status_class}">{status_text}</span></td>
                <td>{len(result['keywords_found'])}</td>
                <td>{result['confidence']}</td>
                <td>{"✓" if result.get('ethical_statement', {}).get('has_statement') else "✗"}</td>
                <td class="action-cell">
                    <a href="{html_filename}" class="btn-view" target="_blank">查看详情</a>
                </td>
            </tr>
            """
        
        html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>伦理审批检测汇总报告</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
            background: #f5f7fa;
            padding: 40px 20px;
        }}
        
        .container {{
            max-width: 1400px;
            margin: 0 auto;
        }}
        
        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 50px;
            border-radius: 20px 20px 0 0;
            box-shadow: 0 10px 40px rgba(0, 0, 0, 0.1);
        }}
        
        .header h1 {{
            font-size: 36px;
            margin-bottom: 10px;
            font-weight: 600;
        }}
        
        .header .subtitle {{
            font-size: 16px;
            opacity: 0.9;
        }}
        
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 24px;
            margin-top: -40px;
            padding: 0 30px;
            margin-bottom: 40px;
        }}
        
        .stat-card {{
            background: white;
            padding: 30px;
            border-radius: 16px;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.08);
            text-align: center;
            transition: transform 0.2s;
        }}
        
        .stat-card:hover {{
            transform: translateY(-5px);
        }}
        
        .stat-number {{
            font-size: 48px;
            font-weight: 700;
            margin-bottom: 8px;
        }}
        
        .stat-label {{
            color: #6b7280;
            font-size: 14px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        
        .stat-danger {{ color: #ef4444; }}
        .stat-success {{ color: #10b981; }}
        .stat-info {{ color: #3b82f6; }}
        .stat-gray {{ color: #6b7280; }}
        
        .content {{
            background: white;
            padding: 40px;
            border-radius: 0 0 20px 20px;
            box-shadow: 0 10px 40px rgba(0, 0, 0, 0.1);
        }}
        
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 20px;
        }}
        
        thead {{
            background: #f9fafb;
        }}
        
        th {{
            padding: 16px;
            text-align: left;
            font-weight: 600;
            color: #374151;
            border-bottom: 2px solid #e5e7eb;
        }}
        
        td {{
            padding: 16px;
            border-bottom: 1px solid #f3f4f6;
        }}
        
        tr:hover {{
            background: #f9fafb;
        }}
        
        .filename-cell a {{
            color: #3b82f6;
            text-decoration: none;
            font-weight: 500;
        }}
        
        .filename-cell a:hover {{
            text-decoration: underline;
        }}
        
        .status-badge {{
            display: inline-block;
            padding: 6px 14px;
            border-radius: 12px;
            font-size: 13px;
            font-weight: 600;
        }}
        
        .status-danger {{
            background: #fee2e2;
            color: #991b1b;
        }}
        
        .status-success {{
            background: #d1fae5;
            color: #065f46;
        }}
        
        .status-info {{
            background: #dbeafe;
            color: #1e40af;
        }}
        
        .status-gray {{
            background: #f3f4f6;
            color: #4b5563;
        }}
        
        .btn-view {{
            display: inline-block;
            padding: 8px 16px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            text-decoration: none;
            border-radius: 8px;
            font-size: 13px;
            font-weight: 500;
            transition: transform 0.2s;
        }}
        
        .btn-view:hover {{
            transform: scale(1.05);
        }}
        
        .footer {{
            text-align: center;
            margin-top: 40px;
            color: #6b7280;
            font-size: 14px;
        }}
        
        @media (max-width: 768px) {{
            .stats-grid {{
                grid-template-columns: 1fr;
            }}
            
            table {{
                font-size: 14px;
            }}
            
            th, td {{
                padding: 12px 8px;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📊 伦理审批检测汇总报告</h1>
            <div class="subtitle">Ethics Approval Detection Summary Report</div>
        </div>
        
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-number">{len(results)}</div>
                <div class="stat-label">总文件数</div>
            </div>
            <div class="stat-card">
                <div class="stat-number stat-danger">{len(needs_ethics)}</div>
                <div class="stat-label">需要审批</div>
            </div>
            <div class="stat-card">
                <div class="stat-number stat-success">{len(no_ethics)}</div>
                <div class="stat-label">不需要审批</div>
            </div>
            <div class="stat-card">
                <div class="stat-number stat-info">{len(likely_no)}</div>
                <div class="stat-label">可能不需要</div>
            </div>
        </div>
        
        <div class="content">
            <h2 style="margin-bottom: 20px; color: #1f2937;">检测结果详情</h2>
            <table>
                <thead>
                    <tr>
                        <th style="width: 50px;">#</th>
                        <th>文件名称</th>
                        <th style="width: 120px;">检测结果</th>
                        <th style="width: 100px;">关键词数</th>
                        <th style="width: 100px;">置信度</th>
                        <th style="width: 120px;">Ethical Statement</th>
                        <th style="width: 100px;">操作</th>
                    </tr>
                </thead>
                <tbody>
                    {table_rows}
                </tbody>
            </table>
        </div>
        
        <div class="footer">
            <p>本报告由学术稿件伦理审批检测系统自动生成</p>
            <p style="margin-top: 8px;">Powered by DeepSeek AI | Generated on {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
        </div>
    </div>
</body>
</html>"""
        
        # 保存汇总HTML
        summary_path = Path(output_dir) / "index.html"
        with open(summary_path, 'w', encoding='utf-8') as f:
            f.write(html_content)


# 使用示例
if __name__ == "__main__":
    # 请通过环境变量 DEEPSEEK_API_KEY 或 config.json 配置密钥。
    detector = EthicsContentDetector()
    
    # 批量处理整个文件夹，生成HTML报告
    results = detector.batch_process(
        folder_path="./papers",      # 你的稿件文件夹路径
        output_dir="./reports"       # HTML报告输出目录
    )
    
    print("\n处理完成！")
    print("请打开 reports/index.html 查看汇总报告")
