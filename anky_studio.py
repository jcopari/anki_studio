import asyncio
import csv
import os
import zlib
import threading
import tempfile
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import edge_tts
import genanki
import shutil
import re

# --- CONFIGURAÇÃO GLOBAL DE VOZES ---
VOICES = {
    "Inglês (US) - Christopher (M)": "en-US-ChristopherNeural",
    "Inglês (US) - Michelle (F)": "en-US-MichelleNeural",
    "Inglês (UK) - Ryan (M)": "en-GB-RyanNeural",
    "Italiano - Diego (M)": "it-IT-DiegoNeural",
    "Italiano - Elsa (F)": "it-IT-ElsaNeural",
    "Espanhol - Alvaro (M)": "es-ES-AlvaroNeural",
    "Alemão - Conrad (M)": "de-DE-ConradNeural"
}

# --- BACKEND ---

class AnkiBuilderBackend:
    def __init__(self, log_callback, progress_callback):
        self.log = log_callback
        self.progress = progress_callback

    async def generate_audio(self, text, filepath, voice, rate, semaphore, max_retries=3):
        async with semaphore:
            if not text or not text.strip(): 
                return False
            
            # Limpeza para TTS
            clean_text = text.replace("\n", " ").strip()
            
            # Retry com backoff exponencial
            for attempt in range(max_retries):
                try:
                    communicate = edge_tts.Communicate(text=clean_text, voice=voice, rate=rate)
                    # FIX-005: Timeout de 30 segundos por arquivo
                    await asyncio.wait_for(
                        communicate.save(filepath),
                        timeout=30.0
                    )
                    return True
                except asyncio.TimeoutError:
                    if attempt < max_retries - 1:
                        wait_time = 2 ** attempt  # 1s, 2s, 4s
                        await asyncio.sleep(wait_time)
                        continue
                    self.log(f"[ERRO TTS] Timeout ao gerar áudio após {max_retries} tentativas")
                    return False
                except (OSError, IOError) as e:
                    # FIX-004: Exceções específicas para I/O
                    self.log(f"[ERRO TTS I/O] Falha ao salvar arquivo: {str(e)}")
                    return False
                except Exception as e:
                    # FIX-004: Capturar outros erros específicos se possível
                    error_type = type(e).__name__
                    if attempt < max_retries - 1:
                        wait_time = 2 ** attempt
                        await asyncio.sleep(wait_time)
                        continue
                    self.log(f"[ERRO TTS] {error_type}: {str(e)}")
                    return False

    async def _run_legacy_pipeline(self, csv_path, voice_code, speed, MODEL_ID, DECK_ID, output_pkg):
        """Modo legado - 7 colunas fixas"""
        base_name = os.path.splitext(os.path.basename(csv_path))[0]
        
        # FIX-014: Sanitizar nome do arquivo
        safe_name = re.sub(r'[<>:"/\\|?*]', '_', base_name)
        safe_name = safe_name[:200]  # Limitar tamanho
        deck = genanki.Deck(DECK_ID, safe_name)
        
        # FIX-008: Validar permissões de escrita
        output_dir = os.path.dirname(os.path.abspath(output_pkg)) or '.'
        if not os.access(output_dir, os.W_OK):
            self.log(f"[ERRO] Sem permissão de escrita em: {output_dir}")
            return False
        
        # --- MODELO DE 7 COLUNAS + 2 GERADAS ---
        model = genanki.Model(
            MODEL_ID,
            'Universal 7-Col Model',
            fields=[
                {'name': 'Target Word'},
                {'name': 'Audio Script'},
                {'name': 'Cloze Sentence'},
                {'name': 'IPA'},
                {'name': 'Simple Definition'},
                {'name': 'PT Translation'},
                {'name': 'Image Query'},
                {'name': 'Audio File'}, # Gerado
                {'name': 'Image File'}, # Reservado (Vazio por enquanto)
            ],
            templates=[{
                'name': 'Card 1',
                'qfmt': '''
                    <div style="display:none">{{Audio File}}</div>
                    <div class="sentence">{{Cloze Sentence}}</div>
                    <br>
                    <div style="color:#888; font-size:14px;">{{type:Target Word}}</div>
                    <div class="hint">Dica
                        <span class="tooltip">{{Simple Definition}}</span>
                    </div>
                ''',
                'afmt': '''
                    <div class="sentence">{{Cloze Sentence}}</div>
                    <hr>
                    {{type:Target Word}}
                    <br>
                    {{Audio File}}
                    <div class="script">{{Audio Script}}</div>
                    <div class="ipa">{{IPA}}</div>
                    <br>
                    <div class="translation">{{PT Translation}}</div>
                ''',
            }],
            css='''
                .card { font-family: Arial; text-align: center; font-size: 20px; background-color: white; }
                .sentence { font-size: 24px; color: #2c3e50; font-weight: bold; margin-bottom: 20px; }
                .script { color: #2980b9; margin-top: 10px; font-weight: 500;}
                .ipa { font-family: "Lucida Console", monospace; color: #888; font-size: 16px; }
                .translation { color: #555; font-style: italic; margin-top: 15px; }
                .hint { font-size: 14px; color: #007bff; cursor: help; margin-top: 20px;}
                .tooltip { visibility: hidden; background-color: #333; color: #fff; text-align: center; border-radius: 6px; padding: 5px; position: absolute; z-index: 1; }
                .hint:hover .tooltip { visibility: visible; }
            '''
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            self.log(f"--- Iniciando (Modo Legado) ---")
            
            media_files = []
            tasks = []
            semaphore = asyncio.Semaphore(20)

            try:
                with open(csv_path, encoding='utf-8-sig') as f:
                    sample = f.read(1024)
                    sniffer = csv.Sniffer()
                    try:
                        dialect = sniffer.sniff(sample)
                    except:
                        dialect = 'excel'
                    f.seek(0)
                    
                    reader = csv.DictReader(f, dialect=dialect)
                    
                    # VALIDAÇÃO DAS 7 COLUNAS
                    required = {'Target Word', 'Audio Script', 'Cloze Sentence', 'IPA', 'Simple Definition', 'PT Translation', 'Image Query'}
                    headers_set = set(reader.fieldnames)
                    
                    if not required.issubset(headers_set):
                        missing = required - headers_set
                        self.log(f"[FATAL] CSV Inválido!")
                        self.log(f"Faltam as colunas: {missing}")
                        return False

                    rows = list(reader)
                    
                    if not rows:
                        self.log(f"[ERRO] CSV está vazio!")
                        return False
                    
                    total_rows = len(rows)
                    
                    # FIX-017: Verificar espaço em disco antes de processar
                    try:
                        free_space = shutil.disk_usage('.').free
                        # Estimar ~50KB por áudio + overhead
                        estimated_size = total_rows * 60_000  # 60KB por item com margem
                        required_space = estimated_size * 1.5  # 50% de margem de segurança
                        
                        if free_space < required_space:
                            self.log(f"[ERRO] Espaço em disco insuficiente!")
                            self.log(f"  Necessário: {required_space / 1_000_000:.1f} MB")
                            self.log(f"  Disponível: {free_space / 1_000_000:.1f} MB")
                            return False
                        else:
                            self.log(f"✓ Espaço em disco: {free_space / 1_000_000:.1f} MB disponível")
                    except Exception as e:
                        self.log(f"[AVISO] Não foi possível verificar espaço em disco: {str(e)}")
                        # Continua mesmo assim, mas avisa
                    
                    # FIX-006: Estatísticas de sucessos/falhas
                    stats = {'success': 0, 'failed': 0, 'skipped': 0}

                    async def process_row(idx, row):
                        script_text = row['Audio Script'].strip()
                        if not script_text: 
                            stats['skipped'] += 1
                            return

                        # FIX-010: Incluir índice no hash para garantir unicidade
                        file_hash = zlib.crc32(f"{idx}{script_text}".encode())
                        audio_filename = f"audio_{idx}_{file_hash}.mp3"
                        audio_path = os.path.join(temp_dir, audio_filename)

                        success = await self.generate_audio(script_text, audio_path, voice_code, speed, semaphore)
                        
                        audio_field = ""
                        if success:
                            media_files.append(audio_path)
                            audio_field = f"[sound:{audio_filename}]"
                            stats['success'] += 1
                        else:
                            stats['failed'] += 1

                        note = genanki.Note(
                            model=model,
                            fields=[
                                row['Target Word'],
                                row['Audio Script'],
                                row['Cloze Sentence'],
                                row['IPA'],
                                row['Simple Definition'],
                                row['PT Translation'],
                                row['Image Query'],
                                audio_field,
                                "" # Image File (Vazio)
                            ]
                        )
                        deck.add_note(note)
                        
                        # FIX-011: Atualizar progresso sempre, log a cada 10
                        self.progress(idx + 1, total_rows)
                        if idx % 10 == 0:
                            self.log(f"[{idx+1}] OK: {row['Target Word']}")

                    # FIX-001: Processamento em lote para evitar OOM
                    BATCH_SIZE = 100
                    for i, row in enumerate(rows):
                        tasks.append(process_row(i, row))
                    
                    # Processar em batches
                    for batch_start in range(0, len(tasks), BATCH_SIZE):
                        batch = tasks[batch_start:batch_start + BATCH_SIZE]
                        await asyncio.gather(*batch)
                        # Pequena pausa entre batches para liberar memória
                        await asyncio.sleep(0.1)
                    
                    # FIX-006: Reportar estatísticas
                    self.log(f"--- Estatísticas: {stats['success']} sucessos, {stats['failed']} falhas, {stats['skipped']} ignorados ---")

            except (IOError, OSError) as e:
                # FIX-004: Exceções específicas para I/O
                self.log(f"[ERRO I/O] Erro ao ler CSV: {type(e).__name__}: {str(e)}")
                return False
            except csv.Error as e:
                # FIX-004: Exceções específicas para CSV
                self.log(f"[ERRO CSV] Erro ao processar CSV: {str(e)}")
                return False
            except Exception as e:
                # FIX-004: Outros erros
                self.log(f"[ERRO] Erro inesperado ao processar CSV: {type(e).__name__}: {str(e)}")
                return False

            self.log(f"--- Empacotando... ---")
            try:
                pkg = genanki.Package(deck)
                pkg.media_files = media_files
                pkg.write_to_file(output_pkg)
            except (IOError, OSError) as e:
                # FIX-004: Exceções específicas para escrita
                self.log(f"[ERRO I/O] Falha ao escrever arquivo: {type(e).__name__}: {str(e)}")
                return False
            except Exception as e:
                self.log(f"[ERRO] Falha ao empacotar deck: {type(e).__name__}: {str(e)}")
                return False
            
            self.progress(total_rows, total_rows)
            self.log(f"--- SUCESSO: {output_pkg} ---")
            return True

    async def run_pipeline(self, csv_path, voice_key, speed, column_mapping=None):
        """
        column_mapping: dict com {
            'audio_source': 'nome_coluna',
            'selected_columns': ['col1', 'col2', ...],
            'all_columns': ['todas', 'colunas']
        }
        """
        try:
            # FIX-008: Validação completa de entrada
            # Validação de arquivo
            if not os.path.exists(csv_path):
                self.log(f"[ERRO] Arquivo não encontrado: {csv_path}")
                return False
            
            # Validação de voice_key
            if voice_key not in VOICES:
                self.log(f"[ERRO] Voz inválida: {voice_key}")
                return False
            
            # Validação de speed
            if not speed or not speed.endswith('%'):
                self.log(f"[ERRO] Velocidade inválida: {speed}. Deve terminar com '%'")
                return False
            try:
                speed_value = int(speed[1:-1])  # Remove + ou - e %
                if not (-50 <= speed_value <= 50):
                    self.log(f"[ERRO] Velocidade fora do range válido (-50% a +50%): {speed}")
                    return False
            except ValueError:
                self.log(f"[ERRO] Velocidade com formato inválido: {speed}")
                return False
            
            voice_code = VOICES[voice_key]
            base_name = os.path.splitext(os.path.basename(csv_path))[0]
            
            # FIX-014: Sanitizar nome do arquivo
            safe_name = re.sub(r'[<>:"/\\|?*]', '_', base_name)
            safe_name = safe_name[:200]  # Limitar tamanho (Windows tem limite de 260 chars)
            output_pkg = f"{safe_name}_Complete.apkg"
            
            # FIX-008: Validar permissões de escrita
            output_dir = os.path.dirname(os.path.abspath(output_pkg)) or '.'
            if not os.access(output_dir, os.W_OK):
                self.log(f"[ERRO] Sem permissão de escrita em: {output_dir}")
                return False

            # IDs Determinísticos
            MODEL_ID = zlib.crc32(f"Dynamic_Model_v1".encode('utf-8'))
            DECK_ID = zlib.crc32(f"Deck_{safe_name}".encode('utf-8'))

            # Se não houver mapeamento, usar modo legado
            if column_mapping is None:
                return await self._run_legacy_pipeline(csv_path, voice_code, speed, MODEL_ID, DECK_ID, output_pkg)
            
            # Modo flexível - usar mapeamento
            audio_source = column_mapping['audio_source']
            audio_target = column_mapping.get('audio_target', audio_source)  # Default: mesma coluna da fonte
            selected_columns = column_mapping['selected_columns']

            # Criar modelo dinâmico: inserir áudio na posição da coluna target
            fields = []
            audio_field_index = None
            for i, col in enumerate(selected_columns):
                if col == audio_target:
                    # Inserir campo de áudio na posição da coluna target (antes da coluna)
                    fields.append({'name': 'Audio File'})
                    audio_field_index = i
                    # Depois inserir a própria coluna
                    fields.append({'name': col})
                else:
                    fields.append({'name': col})
            
            # Se audio_target não estiver nas colunas selecionadas, adicionar no final
            if audio_field_index is None:
                fields.append({'name': 'Audio File'})
                audio_field_index = len(selected_columns)
            
            # Criar template dinâmico
            first_field = selected_columns[0] if selected_columns else 'Field1'
            other_fields_html = '<br>'.join([f'<div class="field">{{{{{col}}}}}</div>' for col in selected_columns[1:]])
            
            model = genanki.Model(
                MODEL_ID,
                'Dynamic Column Model',
                fields=fields,
                templates=[{
                    'name': 'Card 1',
                    'qfmt': f'''
                        <div style="display:none">{{{{Audio File}}}}</div>
                        <div class="sentence">{{{{{first_field}}}}}</div>
                    ''',
                    'afmt': f'''
                        <div class="sentence">{{{{{first_field}}}}}</div>
                        <hr>
                        {{{{Audio File}}}}
                        {other_fields_html if other_fields_html else ''}
                    ''',
                }],
                css='''
                    .card { font-family: Arial; text-align: center; font-size: 20px; background-color: white; }
                    .sentence { font-size: 24px; color: #2c3e50; font-weight: bold; margin-bottom: 20px; }
                    .field { color: #555; margin-top: 10px; }
                '''
            )

            deck = genanki.Deck(DECK_ID, safe_name)
            
            with tempfile.TemporaryDirectory() as temp_dir:
                self.log(f"--- Iniciando: {safe_name} ---")
                self.log(f"--- Colunas selecionadas: {', '.join(selected_columns)} ---")
                self.log(f"--- Fonte de áudio: {audio_source} ---")
                self.log(f"--- Áudio será inserido em: {audio_target} ---")
                
                media_files = []
                tasks = []
                semaphore = asyncio.Semaphore(20)

                try:
                    with open(csv_path, encoding='utf-8-sig') as f:
                        sample = f.read(1024)
                        sniffer = csv.Sniffer()
                        try:
                            dialect = sniffer.sniff(sample)
                        except Exception:
                            dialect = 'excel'
                        f.seek(0)
                        
                        reader = csv.DictReader(f, dialect=dialect)
                        rows = list(reader)
                        
                        if not rows:
                            self.log(f"[ERRO] CSV está vazio!")
                            return False
                        
                        # Verificar se a coluna de áudio existe
                        if audio_source not in reader.fieldnames:
                            self.log(f"[ERRO] Coluna '{audio_source}' não encontrada no CSV!")
                            return False
                        
                        total_rows = len(rows)
                        
                        # FIX-017: Verificar espaço em disco antes de processar
                        try:
                            free_space = shutil.disk_usage('.').free
                            # Estimar ~50KB por áudio + overhead
                            estimated_size = total_rows * 60_000  # 60KB por item com margem
                            required_space = estimated_size * 1.5  # 50% de margem de segurança
                            
                            if free_space < required_space:
                                self.log(f"[ERRO] Espaço em disco insuficiente!")
                                self.log(f"  Necessário: {required_space / 1_000_000:.1f} MB")
                                self.log(f"  Disponível: {free_space / 1_000_000:.1f} MB")
                                return False
                            else:
                                self.log(f"✓ Espaço em disco: {free_space / 1_000_000:.1f} MB disponível")
                        except Exception as e:
                            self.log(f"[AVISO] Não foi possível verificar espaço em disco: {str(e)}")
                            # Continua mesmo assim, mas avisa
                        
                        # FIX-006: Estatísticas de sucessos/falhas
                        stats = {'success': 0, 'failed': 0, 'skipped': 0}

                        async def process_row(idx, row):
                            # Fonte do áudio = coluna escolhida pelo usuário
                            script_text = row.get(audio_source, '').strip()
                            if not script_text: 
                                stats['skipped'] += 1
                                return

                            # Hash único (FIX-010: incluir índice para garantir unicidade)
                            file_hash = zlib.crc32(f"{idx}{script_text}".encode())
                            audio_filename = f"audio_{idx}_{file_hash}.mp3"
                            audio_path = os.path.join(temp_dir, audio_filename)

                            success = await self.generate_audio(script_text, audio_path, voice_code, speed, semaphore)
                            
                            audio_field = ""
                            if success:
                                media_files.append(audio_path)
                                audio_field = f"[sound:{audio_filename}]"
                                stats['success'] += 1
                            else:
                                stats['failed'] += 1

                            # Criar nota com colunas selecionadas, inserindo áudio na posição correta
                            # A ordem deve corresponder exatamente aos campos do modelo
                            note_fields = []
                            for col in selected_columns:
                                if col == audio_target:
                                    # Inserir áudio na posição da coluna target (antes do valor da coluna)
                                    note_fields.append(audio_field)
                                    # Depois inserir o valor da própria coluna
                                    note_fields.append(row.get(col, ''))
                                else:
                                    note_fields.append(row.get(col, ''))
                            
                            # Se audio_target não estiver nas colunas selecionadas, adicionar no final
                            if audio_target not in selected_columns:
                                note_fields.append(audio_field)
                            
                            note = genanki.Note(model=model, fields=note_fields)
                            deck.add_note(note)
                            
                            # FIX-011: Atualizar progresso sempre, log a cada 10
                            self.progress(idx + 1, total_rows)
                            if idx % 10 == 0:
                                first_col_value = row.get(selected_columns[0], 'N/A') if selected_columns else 'N/A'
                                self.log(f"[{idx+1}] OK: {first_col_value}")

                        # FIX-001: Processamento em lote para evitar OOM
                        BATCH_SIZE = 100
                        for i, row in enumerate(rows):
                            tasks.append(process_row(i, row))
                        
                        # Processar em batches
                        for batch_start in range(0, len(tasks), BATCH_SIZE):
                            batch = tasks[batch_start:batch_start + BATCH_SIZE]
                            await asyncio.gather(*batch)
                            # Pequena pausa entre batches para liberar memória
                            await asyncio.sleep(0.1)
                        
                        # FIX-006: Reportar estatísticas
                        self.log(f"--- Estatísticas: {stats['success']} sucessos, {stats['failed']} falhas, {stats['skipped']} ignorados ---")

                except (IOError, OSError) as e:
                    # FIX-004: Exceções específicas para I/O
                    self.log(f"[ERRO I/O] Erro ao ler CSV: {type(e).__name__}: {str(e)}")
                    return False
                except csv.Error as e:
                    # FIX-004: Exceções específicas para CSV
                    self.log(f"[ERRO CSV] Erro ao processar CSV: {str(e)}")
                    return False
                except Exception as e:
                    # FIX-004: Outros erros
                    self.log(f"[ERRO] Erro inesperado ao processar CSV: {type(e).__name__}: {str(e)}")
                    return False

                self.log(f"--- Empacotando... ---")
                try:
                    pkg = genanki.Package(deck)
                    pkg.media_files = media_files
                    pkg.write_to_file(output_pkg)
                except (IOError, OSError) as e:
                    # FIX-004: Exceções específicas para escrita
                    self.log(f"[ERRO I/O] Falha ao escrever arquivo: {type(e).__name__}: {str(e)}")
                    return False
                except Exception as e:
                    self.log(f"[ERRO] Falha ao empacotar deck: {type(e).__name__}: {str(e)}")
                    return False
                
                self.progress(total_rows, total_rows)
                self.log(f"--- SUCESSO: {output_pkg} ---")
                return True
                
        except KeyError as e:
            # FIX-004: Exceções específicas
            self.log(f"[ERRO FATAL] Chave não encontrada: {str(e)}")
            return False
        except Exception as e:
            self.log(f"[ERRO FATAL] {type(e).__name__}: {str(e)}")
            return False


class NarratorBackend:
    def __init__(self, status_callback):
        self.status_callback = status_callback

    async def generate_long_audio(self, text, filepath, voice, speed):
        try:
            # Validação de tamanho (Edge TTS tem limite prático)
            if len(text) > 5000:
                self.status_callback("Aviso: Texto muito longo. Pode ser cortado pelo TTS.")
            
            communicate = edge_tts.Communicate(text, voice, rate=speed)
            # FIX-005: Timeout de 60 segundos para textos longos
            await asyncio.wait_for(
                communicate.save(filepath),
                timeout=60.0
            )
            self.status_callback(f"Salvo com sucesso em: {os.path.basename(filepath)}")
            return True
        except asyncio.TimeoutError:
            # FIX-004: Exceção específica para timeout
            self.status_callback("Erro: Timeout ao gerar áudio (texto muito longo ou rede lenta)")
            return False
        except (OSError, IOError) as e:
            # FIX-004: Exceções específicas para I/O
            self.status_callback(f"Erro I/O: Falha ao salvar arquivo: {str(e)}")
            return False
        except Exception as e:
            # FIX-004: Outros erros
            self.status_callback(f"Erro: {type(e).__name__}: {str(e)}")
            return False


# --- DIÁLOGO DE MAPEAMENTO DE COLUNAS ---

class ColumnMappingDialog(tk.Toplevel):
    """Diálogo para mapear colunas do CSV"""
    
    def __init__(self, parent, csv_path, csv_columns=None):
        super().__init__(parent)
        self.title("Mapear Colunas do CSV")
        self.geometry("700x600")
        self.configure(bg="#f4f4f4")
        self.result = None
        
        # FIX-009: Usar colunas passadas ou detectar se não fornecidas
        if csv_columns:
            self.csv_columns = csv_columns
        else:
            self.csv_columns = self._detect_columns(csv_path)
        
        if not self.csv_columns:
            messagebox.showerror("Erro", "Não foi possível ler as colunas do CSV.")
            self.destroy()
            return
        
        self._setup_ui()
        self.transient(parent)
        self.grab_set()
        
    def _detect_columns(self, csv_path):
        """Detecta as colunas do CSV"""
        try:
            with open(csv_path, encoding='utf-8-sig') as f:
                sample = f.read(1024)
                sniffer = csv.Sniffer()
                try:
                    dialect = sniffer.sniff(sample)
                except Exception:
                    dialect = 'excel'
                f.seek(0)
                reader = csv.DictReader(f, dialect=dialect)
                return reader.fieldnames or []
        except (IOError, OSError) as e:
            # FIX-004: Exceções específicas
            return None
        except Exception as e:
            return None
    
    def _setup_ui(self):
        main_frame = ttk.Frame(self, padding=20)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Título
        title = ttk.Label(main_frame, text="Colunas detectadas no CSV:", font=("Arial", 10, "bold"))
        title.pack(anchor='w', pady=(0, 10))
        
        # Frame com scroll para colunas
        canvas_frame = ttk.Frame(main_frame)
        canvas_frame.pack(fill=tk.BOTH, expand=True, pady=10)
        
        canvas = tk.Canvas(canvas_frame, bg="white")
        scrollbar = ttk.Scrollbar(canvas_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        # Variáveis para armazenar escolhas
        self.audio_source_var = tk.StringVar()
        self.audio_target_var = tk.StringVar()
        self.column_mapping = {}  # {coluna_csv: usar_ou_nao}
        
        # Criar checkboxes para cada coluna
        ttk.Label(scrollable_frame, text="Selecione quais colunas usar no deck:", font=("Arial", 9)).pack(anchor='w', pady=5)
        
        for col in self.csv_columns:
            frame = ttk.Frame(scrollable_frame)
            frame.pack(fill=tk.X, pady=2)
            
            var = tk.BooleanVar(value=True)  # Por padrão, todas selecionadas
            self.column_mapping[col] = var
            
            ttk.Checkbutton(frame, text=col, variable=var).pack(side=tk.LEFT, padx=5)
        
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        # Seleção da fonte de áudio
        audio_frame = ttk.LabelFrame(main_frame, text="Configuração de Áudio", padding=10)
        audio_frame.pack(fill=tk.X, pady=10)
        
        # Fonte do áudio
        ttk.Label(audio_frame, text="Qual coluna será usada para gerar o áudio?").pack(anchor='w')
        audio_source_combo = ttk.Combobox(audio_frame, textvariable=self.audio_source_var, 
                                         values=self.csv_columns, state="readonly", width=40)
        audio_source_combo.pack(fill=tk.X, pady=5)
        
        # Callback para atualizar o target quando source mudar
        def update_audio_target(*args):
            if not self.audio_target_var.get() or self.audio_target_var.get() not in self.csv_columns:
                self.audio_target_var.set(self.audio_source_var.get())
        
        self.audio_source_var.trace('w', update_audio_target)
        
        # Onde inserir o áudio
        ttk.Label(audio_frame, text="Onde o áudio será inserido no card?").pack(anchor='w', pady=(10, 0))
        audio_target_combo = ttk.Combobox(audio_frame, textvariable=self.audio_target_var, 
                                         values=self.csv_columns, state="readonly", width=40)
        audio_target_combo.pack(fill=tk.X, pady=5)
        
        # Se houver coluna "Audio Script", selecionar por padrão
        if "Audio Script" in self.csv_columns:
            self.audio_source_var.set("Audio Script")
            self.audio_target_var.set("Audio Script")
        elif self.csv_columns:
            self.audio_source_var.set(self.csv_columns[0])
            self.audio_target_var.set(self.csv_columns[0])
        
        # Botões
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=10)
        
        ttk.Button(btn_frame, text="Cancelar", command=self.cancel).pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_frame, text="Confirmar", command=self.confirm).pack(side=tk.RIGHT)
    
    def confirm(self):
        if not self.audio_source_var.get():
            messagebox.showwarning("Aviso", "Selecione a coluna fonte do áudio.")
            return
        
        if not self.audio_target_var.get():
            messagebox.showwarning("Aviso", "Selecione onde o áudio será inserido.")
            return
        
        # Coletar colunas selecionadas
        selected_columns = [col for col, var in self.column_mapping.items() if var.get()]
        
        if not selected_columns:
            messagebox.showwarning("Aviso", "Selecione pelo menos uma coluna.")
            return
        
        # Verificar se a coluna target está nas selecionadas
        audio_target = self.audio_target_var.get()
        if audio_target not in selected_columns:
            messagebox.showwarning("Aviso", f"A coluna '{audio_target}' (onde o áudio será inserido) deve estar selecionada.")
            return
        
        self.result = {
            'audio_source': self.audio_source_var.get(),
            'audio_target': audio_target,
            'selected_columns': selected_columns,
            'all_columns': self.csv_columns
        }
        self.destroy()
    
    def cancel(self):
        self.result = None
        self.destroy()


# --- GUI UNIFICADA ---

class AnkiStudioApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Anki Studio - Gerador de Decks e Graded Readers")
        self.geometry("750x650")
        self.configure(bg="#f4f4f4")
        
        # Configurar event loop para Windows
        if os.name == 'nt':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
        self._setup_ui()

    def _setup_ui(self):
        # Notebook para abas
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Aba 1: Gerar Flashcards Anki
        self.anki_frame = ttk.Frame(self.notebook, padding=20)
        self.notebook.add(self.anki_frame, text="📚 Gerar Flashcards Anki")
        self._setup_anki_tab()
        
        # Aba 2: Gerar Graded Readers
        self.narrator_frame = ttk.Frame(self.notebook, padding=20)
        self.notebook.add(self.narrator_frame, text="🎙️ Gerar Graded Reader")
        self._setup_narrator_tab()

    def _setup_anki_tab(self):
        # Variáveis
        self.anki_file_path = tk.StringVar()
        self.anki_voice_var = tk.StringVar(value="Inglês (US) - Christopher (M)")
        self.anki_speed_var = tk.StringVar(value="+20%")
        
        # Header CSV
        lbl = ttk.Label(self.anki_frame, text="Arquivo CSV (O programa detectará automaticamente as colunas)", font=("Arial", 9, "bold"))
        lbl.pack(anchor='w')
        
        f_file = ttk.Frame(self.anki_frame)
        f_file.pack(fill=tk.X, pady=5)
        ttk.Entry(f_file, textvariable=self.anki_file_path).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(f_file, text="Selecionar", command=self.browse_anki).pack(side=tk.LEFT, padx=5)

        # Configs
        f_cfg = ttk.Frame(self.anki_frame)
        f_cfg.pack(fill=tk.X, pady=15)
        
        ttk.Label(f_cfg, text="Idioma/Voz:").pack(side=tk.LEFT)
        ttk.Combobox(f_cfg, textvariable=self.anki_voice_var, values=list(VOICES.keys()), state="readonly", width=30).pack(side=tk.LEFT, padx=5)
        
        ttk.Label(f_cfg, text="Velocidade:").pack(side=tk.LEFT, padx=(15,0))
        ttk.Combobox(f_cfg, textvariable=self.anki_speed_var, values=["+0%", "+10%", "+20%", "+30%"], state="readonly", width=8).pack(side=tk.LEFT, padx=5)

        # Botão Run
        self.anki_progress_bar = ttk.Progressbar(self.anki_frame, orient=tk.HORIZONTAL, mode='determinate')
        self.anki_progress_bar.pack(fill=tk.X, pady=(10, 5))
        
        self.anki_btn_run = tk.Button(self.anki_frame, text="GERAR DECK COMPLETO", bg="#333", fg="white", font=("Segoe UI", 10, "bold"), command=self.start_anki)
        self.anki_btn_run.pack(fill=tk.X, pady=5)

        # Log
        self.anki_log_text = tk.Text(self.anki_frame, height=12, font=("Consolas", 8), state='disabled', bg="#fff")
        self.anki_log_text.pack(fill=tk.BOTH, expand=True, pady=10)

    def _setup_narrator_tab(self):
        # Variáveis
        self.narrator_voice_var = tk.StringVar(value="Inglês (US) - Christopher (M)")
        self.narrator_speed_var = tk.StringVar(value="+0% (Normal)")
        
        # Container Principal
        main = ttk.Frame(self.narrator_frame)
        main.pack(fill=tk.BOTH, expand=True)

        # 1. Configurações
        top_frame = ttk.LabelFrame(main, text="Configurações de Voz", padding=10)
        top_frame.pack(fill=tk.X, pady=(0, 15))

        # Voz
        ttk.Label(top_frame, text="Narrador:").pack(side=tk.LEFT)
        ttk.Combobox(top_frame, textvariable=self.narrator_voice_var, values=list(VOICES.keys()), state="readonly", width=30).pack(side=tk.LEFT, padx=10)

        # Velocidade
        ttk.Label(top_frame, text="Velocidade:").pack(side=tk.LEFT, padx=(10, 0))
        speed_opts = ["-20% (Muito Lento)", "-10% (Lento)", "+0% (Normal)", "+10% (Rápido)", "+20% (Nativo)"]
        self.narrator_speed_combo = ttk.Combobox(top_frame, textvariable=self.narrator_speed_var, values=speed_opts, state="readonly", width=20)
        self.narrator_speed_combo.pack(side=tk.LEFT, padx=5)

        # 2. Área de Texto
        lbl_text = ttk.Label(main, text="Cole sua história abaixo:", font=("Arial", 10, "bold"))
        lbl_text.pack(anchor="w")

        self.narrator_text_area = scrolledtext.ScrolledText(main, height=15, font=("Georgia", 11), wrap=tk.WORD, undo=True)
        self.narrator_text_area.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # Dica
        tip = ttk.Label(main, text="Dica: O Edge TTS lida bem com pontuação. Use vírgulas e pontos para criar pausas naturais.", foreground="#666", font=("Arial", 8))
        tip.pack(anchor="w", pady=(0, 10))

        # 3. Botão de Ação
        self.narrator_btn_save = tk.Button(main, text="GERAR MP3 DA HISTÓRIA", bg="#27ae60", fg="white", font=("Segoe UI", 11, "bold"), height=2, command=self.save_narrator_audio)
        self.narrator_btn_save.pack(fill=tk.X)

        # Status
        self.narrator_status_var = tk.StringVar(value="Pronto")
        self.narrator_status_bar = ttk.Label(main, textvariable=self.narrator_status_var, relief=tk.SUNKEN, anchor="e")
        self.narrator_status_bar.pack(fill=tk.X, pady=(10, 0))

    # Métodos para aba Anki
    def log_anki(self, msg):
        self.anki_log_text.config(state='normal')
        self.anki_log_text.insert(tk.END, msg + "\n")
        self.anki_log_text.see(tk.END)
        self.anki_log_text.config(state='disabled')

    def update_anki_progress(self, curr, total):
        self.anki_progress_bar['maximum'] = total
        self.anki_progress_bar['value'] = curr

    def _detect_csv_columns(self, csv_path):
        """FIX-009: Detecta colunas do CSV uma vez para evitar leitura duplicada"""
        try:
            with open(csv_path, encoding='utf-8-sig') as f:
                sample = f.read(1024)
                sniffer = csv.Sniffer()
                try:
                    dialect = sniffer.sniff(sample)
                except Exception:
                    dialect = 'excel'
                f.seek(0)
                reader = csv.DictReader(f, dialect=dialect)
                return reader.fieldnames or []
        except (IOError, OSError) as e:
            return None
        except Exception as e:
            return None

    def browse_anki(self):
        f = filedialog.askopenfilename(filetypes=[("CSV", "*.csv")])
        if f: 
            # Definir o caminho do arquivo primeiro (para mostrar no campo)
            self.anki_file_path.set(f)
            # Forçar atualização do Entry
            self.update_idletasks()
            
            # FIX-009: Detectar colunas uma vez e passar para o diálogo
            columns = self._detect_csv_columns(f)
            if not columns:
                messagebox.showerror("Erro", "Não foi possível ler as colunas do CSV.")
                return
            
            # Abrir diálogo de mapeamento passando colunas já detectadas
            dialog = ColumnMappingDialog(self, f, columns)
            self.wait_window(dialog)
            
            if dialog.result:
                self.column_mapping = dialog.result
                self.log_anki(f"✓ CSV carregado: {len(dialog.result['selected_columns'])} colunas selecionadas")
                self.log_anki(f"✓ Fonte de áudio: {dialog.result['audio_source']}")
                self.log_anki(f"✓ Áudio será inserido em: {dialog.result.get('audio_target', dialog.result['audio_source'])}")
            else:
                # Usuário cancelou o mapeamento, mas mantém o arquivo selecionado
                # Limpar apenas o mapeamento, não o arquivo
                if hasattr(self, 'column_mapping'):
                    delattr(self, 'column_mapping')
                self.log_anki("⚠ Mapeamento cancelado. Selecione o arquivo novamente para configurar.")

    def start_anki(self):
        if not self.anki_file_path.get():
            messagebox.showwarning("Aviso", "Selecione o CSV.")
            return
        
        # Verificar se há mapeamento (modo flexível) ou usar modo legado
        column_mapping = getattr(self, 'column_mapping', None)
        
        self.anki_btn_run.config(state='disabled')
        self.anki_log_text.config(state='normal')
        self.anki_log_text.delete(1.0, tk.END)
        self.anki_log_text.config(state='disabled')
        
        backend = AnkiBuilderBackend(self.log_anki, self.update_anki_progress)
        # FIX-015: Thread não-daemon para garantir conclusão
        thread = threading.Thread(target=self.run_anki_thread, args=(backend, column_mapping), daemon=False)
        thread.start()
        # Armazenar thread para possível join futuro
        self.anki_thread = thread

    def run_anki_thread(self, backend, column_mapping):
        csv_f = self.anki_file_path.get()
        voice = self.anki_voice_var.get()
        speed = self.anki_speed_var.get()
        
        success = asyncio.run(backend.run_pipeline(csv_f, voice, speed, column_mapping))
        
        # Atualizar UI na thread principal
        self.after(0, lambda: self.finish_anki_process(success))

    def finish_anki_process(self, success):
        self.anki_btn_run.config(state='normal')
        if success:
            messagebox.showinfo("Sucesso", "Deck gerado com sucesso!")
        else:
            messagebox.showerror("Erro", "Houve um erro ao gerar o deck. Verifique o log.")

    # Métodos para aba Narrator
    def get_clean_speed(self):
        raw = self.narrator_speed_var.get()
        return raw.split(" ")[0]

    def save_narrator_audio(self):
        text_content = self.narrator_text_area.get("1.0", tk.END).strip()
        
        if not text_content:
            messagebox.showwarning("Aviso", "A caixa de texto está vazia.")
            return

        # Escolher onde salvar
        file_path = filedialog.asksaveasfilename(
            defaultextension=".mp3",
            filetypes=[("MP3 Audio", "*.mp3")],
            title="Salvar Narração Como..."
        )

        if not file_path:
            return

        # Bloqueia UI
        self.narrator_btn_save.config(state="disabled", text="GERANDO ÁUDIO... AGUARDE")
        self.narrator_text_area.config(state="disabled")
        self.narrator_status_var.set("Processando texto...")

        # FIX-015: Thread não-daemon para garantir conclusão
        thread = threading.Thread(target=self.run_narrator_thread, args=(text_content, file_path), daemon=False)
        thread.start()
        # Armazenar thread para possível join futuro
        self.narrator_thread = thread

    def run_narrator_thread(self, text, filepath):
        voice_key = self.narrator_voice_var.get()
        voice_code = VOICES[voice_key]
        speed = self.get_clean_speed()

        backend = NarratorBackend(self.update_narrator_status)
        success = asyncio.run(backend.generate_long_audio(text, filepath, voice_code, speed))

        # Restaura UI na thread principal
        self.after(0, lambda: self.finish_narrator_process(success))

    def update_narrator_status(self, message):
        # Thread-safe update
        self.after(0, lambda: self.narrator_status_var.set(message))

    def finish_narrator_process(self, success):
        self.narrator_btn_save.config(state="normal", text="GERAR MP3 DA HISTÓRIA")
        self.narrator_text_area.config(state="normal")
        if success:
            messagebox.showinfo("Sucesso", "Narração concluída!")
        else:
            messagebox.showerror("Erro", "Houve um erro ao gerar a narração.")


if __name__ == "__main__":
    app = AnkiStudioApp()
    app.mainloop()
